import os
import os.path
import fabric
from fabric import api, contrib # why doesn't contrib.files get found below?
from collective.hostout.hostout import buildoutuser
from fabric.context_managers import cd
from pkg_resources import resource_filename


def setupusers():
    """ create users if needed """

    hostout = api.env.get('hostout')
    buildout = api.env['buildout-user']
    effective = api.env['effective-user']
    buildoutgroup = api.env['buildout-group']
    owner = buildout
    
    api.sudo('groupadd %s || echo "group exists"' % buildoutgroup)
    addopt = "--no-user-group -M -g %s" % buildoutgroup
    api.sudo('egrep ^%(owner)s: /etc/passwd || useradd %(owner)s %(addopt)s' % dict(owner=owner, addopt=addopt))
    api.sudo('egrep ^%(effective)s: /etc/passwd || useradd %(effective)s %(addopt)s' % dict(effective=effective, addopt=addopt))
    api.sudo('gpasswd -a %(owner)s %(buildoutgroup)s' % dict(owner=owner, buildoutgroup=buildoutgroup))
    api.sudo('gpasswd -a %(effective)s %(buildoutgroup)s' % dict(effective=effective, buildoutgroup=buildoutgroup))

    #Copy authorized keys to buildout user:
    key_filename, key = api.env.hostout.getIdentityKey()
    for owner in [api.env['buildout-user']]:
        api.sudo("mkdir -p ~%s/.ssh" % owner)
        api.sudo('touch ~%s/.ssh/authorized_keys' % owner)
        contrib.files.append(key, '~%s/.ssh/authorized_keys' % owner, use_sudo=True)
        api.sudo("chown -R %(owner)s ~%(owner)s/.ssh" % locals() )
    

def setowners():
    """ Ensure ownership and permissions are correct on buildout and cache """
    hostout = api.env.get('hostout')
    buildout = api.env['buildout-user']
    effective = api.env['effective-user']
    buildoutgroup = api.env['buildout-group']
    owner = buildout


    path = api.env.path
    bc = hostout.buildout_cache
    dl = hostout.getDownloadCache()
    dist = os.path.join(dl, 'dist')
    bc = hostout.getEggCache()
    var = os.path.join(path, 'var')
    
    # What we want is for
    # - login user to own the buildout and the cache.
    # - effective user to be own the var dir + able to read buildout and cache.
    
    api.sudo('chown -R %(buildout)s:%(buildoutgroup)s %(path)s && '
             ' chmod -R u+rw,g+r-w,o-rw %(path)s' % locals())
    api.sudo('chmod g+x `find %(path)s -perm -u+x`' % locals()) #so effective can execute code
    api.sudo('chmod g+s `find %(path)s -type d`' % locals()) # so new files will keep same group
    api.sudo('mkdir -p %(var)s && chown -R %(effective)s:%(buildoutgroup)s %(var)s && '
             ' chmod -R u+rw,g+wrs,o-rw %(var)s ' % locals())
    
    for cache in [bc, dl, bc]:
        #HACK Have to deal with a shared cache. maybe need some kind of group
        api.sudo('mkdir -p %(cache)s && chown -R %(buildout)s:%(buildoutgroup)s %(cache)s && '
                 ' chmod -R u+rw,a+r %(cache)s ' % locals())


def initcommand(cmd):
    if cmd in ['uploadeggs','uploadbuildout','buildout','run']:
        api.env.user = api.env.hostout.options['buildout-user']
    else:
        api.env.user = api.env.hostout.options['user']
    key_filename = api.env.get('identity-file')
    if key_filename and os.path.exists(key_filename):
        api.env.key_filename = key_filename

def deploy():
    "predeploy, uploadeggs, uploadbuildout, buildout and then postdeploy"
    hostout = api.env['hostout']
    hostout.predeploy()
    hostout.uploadeggs()
    hostout.uploadbuildout()
    hostout.buildout()
    hostout.postdeploy()

def predeploy():
    """Perform any initial plugin tasks. Call bootstrap if needed"""
    hostout = api.env['hostout']

    #run('export http_proxy=localhost:8123') # TODO get this from setting

    path = api.env.path
    api.env.cwd = ''

    #if not contrib.files.exists(hostout.options['path'], use_sudo=True):
    try:
        api.sudo("ls  %(path)s/bin/buildout " % locals(), pty=True)
    except:
        hostout.bootstrap()
        hostout.setowners()

    api.env.cwd = api.env.path
    for cmd in hostout.getPreCommands():
        api.sudo('sh -c "%s"'%cmd)
    api.env.cwd = ''


def bootstrap():
#    api.env.hostout.setupusers()

    # bootstrap assumes that correct python is already installed
    path = api.env.path
    buildout = api.env['buildout-user']
    buildoutgroup = api.env['buildout-group']
    api.sudo('mkdir -p %(path)s' % locals())
    api.sudo('chown -R %(buildout)s:%(buildoutgroup)s %(path)s'%locals())

    buildoutcache = api.env['buildout-cache']
    api.sudo('mkdir -p %s/eggs' % buildoutcache)
    api.sudo('mkdir -p %s/downloads/dist' % buildoutcache)
    api.sudo('mkdir -p %s/extends' % buildoutcache)
    api.sudo('chown -R %s:%s %s' % (buildout, buildoutgroup, buildoutcache))
    api.env.cwd = api.env.path
   
    bootstrap = resource_filename(__name__, 'bootstrap.py')
    api.put(bootstrap, '%s/bootstrap.py' % path)
    
    # put in simplest buildout to get bootstrap to run
    api.sudo('echo "[buildout]" > buildout.cfg')

    version = api.env['python-version']
    major = '.'.join(version.split('.')[:2])

    api.sudo('python%(major)s bootstrap.py --distribute' % locals())


@buildoutuser
def uploadeggs():
    """Release developer eggs and send to host """
    
    hostout = api.env['hostout']

    #need to send package. cycledown servers, install it, run buildout, cycle up servers

    dl = hostout.getDownloadCache()
    contents = api.run('ls %s/dist' % dl).split()

    for pkg in hostout.localEggs():
        name = os.path.basename(pkg)
        if name not in contents:
            tmp = os.path.join('/tmp', name)
            api.put(pkg, tmp)
            api.run("mv -f %(tmp)s %(tgt)s && "
                "chown %(buildout)s %(tgt)s && "
                "chmod a+r %(tgt)s" % dict(
                    tmp = tmp,
                    tgt = os.path.join(dl, 'dist', name),
                    buildout=api.env.hostout.options['buildout-user'],
                    ))

@buildoutuser
def uploadbuildout():
    """Upload buildout pinned version of buildouts to host """
    hostout = api.env.hostout
    buildout = api.env['buildout-user']

    package = hostout.getHostoutPackage()
    tmp = os.path.join('/tmp', os.path.basename(package))
    tgt = os.path.join(hostout.getDownloadCache(), 'dist', os.path.basename(package))

    #api.env.warn_only = True
    if api.run("test -f %(tgt)s || echo 'None'" %locals()) == 'None' :
        api.put(package, tmp)
        api.run("mv %(tmp)s %(tgt)s" % locals() )
        #sudo('chown $(effectiveuser) %s' % tgt)


    user=hostout.options['buildout-user']
    install_dir=hostout.options['path']
    with cd(install_dir):
        api.run('tar -p -xvf %(tgt)s' % locals())
    hostout.setowners()

@buildoutuser
def buildout():
    """Run the buildout on the remote server """

    hostout = api.env.hostout
    hostout_file=hostout.getHostoutFile()
    #api.env.user = api.env['effective-user']
    api.env.cwd = hostout.remote_dir
    api.run('bin/buildout -c %(hostout_file)s' % locals())
    #api.sudo('sudo -u $(effectiveuser) sh -c "export HOME=~$(effectiveuser) && cd $(install_dir) && bin/buildout -c $(hostout_file)"')

#    sudo('chmod 600 .installed.cfg')
#    sudo('find $(install_dir)  -type d -name var -exec chown -R $(effectiveuser) \{\} \;')
#    sudo('find $(install_dir)  -type d -name LC_MESSAGES -exec chown -R $(effectiveuser) \{\} \;')
#    sudo('find $(install_dir)  -name runzope -exec chown $(effectiveuser) \{\} \;')
    hostout.setowners()



def postdeploy():
    """Perform any final plugin tasks """
    
    hostout = api.env.get('hostout')

    api.env.cwd = api.env.path
    hostout_file=hostout.getHostoutFile()
    sudoparts = hostout.options.get('sudo-parts',None)
    if sudoparts:
        api.sudo('bin/buildout -c %(hostout_file)s install %(sudoparts)s' % locals())

 
    api.env.cwd = api.env.path
    for cmd in hostout.getPostCommands():
        api.sudo('sh -c "%s"'%cmd)

@buildoutuser
def run(*cmd):
    """Execute cmd on remote as login user """
    api.env.cwd = api.env.path
    api.run(' '.join(cmd))

def sudo(*cmd):
    """Execute cmd on remote as root user """
    api.env.cwd = api.env.path
    api.sudo(' '.join(cmd))


