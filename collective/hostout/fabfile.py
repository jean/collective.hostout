import os
import os.path
from fabric import api, contrib
from fabric.contrib.files import append
import fabric.contrib.project
from collective.hostout.hostout import buildoutuser, asbuildoutuser
from fabric.context_managers import cd
from pkg_resources import resource_filename
import tempfile
    

@buildoutuser
def run(*cmd):
    """Execute cmd on remote as login user """
    with cd( api.env.path):
        api.run(' '.join(cmd))

def sudo(*cmd):
    """Execute cmd on remote as root user """
    with cd( api.env.path):
        api.sudo(' '.join(cmd))

def put(file, target=None):
    """Recursively upload specified files into the remote buildout folder"""
    if os.path.isdir(file):
        uploads = os.walk(file)
    else:
        uploads = None, None, [file]
    with asbuildoutuser():
        for root, dirs, files in uploads:
            for dir in dirs:
                with cd(api.env.path):
                    api.run('mkdir -p %s'% root +'/'+ dir)
            for file in files:
                file = root + '/' + file
                print file
                if not target:
                    target = file
                if target[0] != '/':
                    target = api.env.path + '/' + target
                api.put(file, target)

def putrsync(dir):
    """ rsync a local buildout folder with the remote buildout """
    with asbuildoutuser():
        parent = '/'.join(dir.split('/')[:-1])
        remote = api.env.path + '/' + parent

        fabric.contrib.project.rsync_project(remote_dir=remote, local_dir = dir)

@buildoutuser
def get(file, target=None):
    """Download the specified files from the remote buildout folder"""
    if not target:
        target = file
    with cd(api.env.path):
        api.get(file, target)

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

    hasBuildoutUser = True
    hasBuildout = True
    if not os.path.exists(api.env.get('identity-file')):
        hasBuildoutUser = False
    else:
        with asbuildoutuser():
            try:
                api.run("[ -e %s/bin/buildout ]"%api.env.path, pty=True)
            except:
                hasBuildout = False
    
    if not hasBuildoutUser or not hasBuildout:
        api.env.hostout.bootstrap()
        api.env.hostout.setowners()

    api.env.hostout.precommands()

    return api.env.superfun()

def precommands():
    "run 'pre-commands' as sudo before deployment"
    hostout = api.env['hostout']
    with cd(api.env.path):
        for cmd in hostout.getPreCommands():
            api.sudo('sh -c "%s"'%cmd)


# Make uploadeggs, uploadbuildout and buildout run independent of each other
# uploadeggs should upload the eggs and write out the versions to a versions file on the host
# uploadbuildout should upload buildout + dependencies but no version pinning
# buildout should upload just the generated cfg which instructs which buildout to r
# un. This step should pin versions
# if buildout is run without uploadeggs then no pinned dev eggs versions exist. in which case need
# to upload dummy pinned versions file.

# buildout will upload file like staging_20100411-23:04:04-[uid].cfg 
# which extends=staging.cfg hostoutversions.cfg devpins.cfg 

# scenarios
# using buildout only
# use uploadbuildout and buildout
# use uploadeggs and then later buildout

# secondary benifit would be to have a set of files which you could roll back easily to a previous
# buildout version including all the dev eggs.



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
    # Ensure there is no local pinned.cfg so we don't clobber it
    # Now upload pinned.cfg. 
    pinned = "[buildout]\ndevelop=\n[versions]\n"+hostout.packages.developVersions()
    tmp = tempfile.NamedTemporaryFile()
    tmp.write(pinned)
    tmp.flush()
    api.put(tmp.name, api.env.path+'/pinned.cfg')
    tmp.close()

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
#    hostout.setowners()

@buildoutuser
def buildout(*args):
    """ Run the buildout on the remote server """

    hostout = api.env.hostout
    hostout_file=hostout.getHostoutFile()
    
    #upload generated cfg with hostout versions
    hostout.getHostoutPackage() # we need this work out releaseid
    filename = "%s-%s.cfg" % (hostout.name, hostout.releaseid) 
    
    with cd(api.env.path):
        tmp = tempfile.NamedTemporaryFile()
        tmp.write(hostout_file)
        tmp.flush()
        api.put(tmp.name, api.env.path+'/'+filename)
        tmp.close()

            #if no pinned.cfg then upload empty one
        if not contrib.files.exists('pinned.cfg'):
            pinned = "[buildout]"
            contrib.files.append(pinned, 'pinned.cfg')
        #run generated buildout
        api.run('bin/buildout -c %s %s' % (filename, ' '.join(args)))

def postdeploy():
    """Perform any final plugin tasks """

    hostout = api.env.get('hostout')
    #hostout.setowners()

    hostout.getHostoutPackage() # we need this work out releaseid
    filename = "%s-%s.cfg" % (hostout.name, hostout.releaseid)
    sudoparts = ' '.join(hostout.options.get('sudo-parts','').split())
    if sudoparts:
        with cd(api.env.path):
            api.sudo('bin/buildout -c %(filename)s install %(sudoparts)s' % locals())
 
    with cd(api.env.path):
        for cmd in hostout.getPostCommands():
            api.sudo('sh -c "%s"'%cmd)


def bootstrap():
    """ Install packages and users needed to get buildout running """
    hostos = api.env.get('hostos','').lower()
    version = api.env['python-version']
    major = '.'.join(version.split('.')[:2])
    majorshort = major.replace('.','')
    d = dict(major=major)

    if not hostos:
        hostos = api.env.hostout.detecthostos().lower()
        
    cmd = getattr(api.env.hostout, 'bootstrap_users_%s'%hostos, api.env.hostout.bootstrap_users)
    cmd()

    #if api.run('python%(major)s --version'%d).succeeded:
    try:
        api.run('python%(major)s --version'%d)
    except:
        cmd = getattr(api.env.hostout, 'bootstrap_python_%s'%hostos, api.env.hostout.bootstrap_python)
        cmd()

    path = api.env.path
    buildout = api.env['buildout-user']
    buildoutgroup = api.env['buildout-group']
    api.sudo('mkdir -p %(path)s & chown %(buildout)s:%(buildoutgroup)s %(path)s' % dict(
        path=path,
        buildout=buildout,
        buildoutgroup=buildoutgroup,
    ))

    buildoutcache = api.env['buildout-cache']
    api.sudo('mkdir -p %s/eggs' % buildoutcache)
    api.sudo('mkdir -p %s/downloads/dist' % buildoutcache)
    api.sudo('mkdir -p %s/extends' % buildoutcache)
    api.sudo('chown -R %s:%s %s' % (buildout, buildoutgroup, buildoutcache))

    cmd = getattr(api.env.hostout, 'bootstrap_buildout_%s'%hostos, api.env.hostout.bootstrap_buildout)
    cmd()


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
    
    api.sudo("find %(path)s  -maxdepth 0 ! -name var -exec chown -R %(buildout)s:%(buildoutgroup)s '{}' \; "
             " -exec chmod -R u+rw,g+r-w,o-rw '{}' \;" % locals())
    api.sudo('mkdir -p %(var)s && chown -R %(effective)s:%(buildoutgroup)s %(var)s && '
             ' chmod -R u+rw,g+wrs,o-rw %(var)s ' % locals())
#    api.sudo("chmod g+x `find %(path)s -perm -g-x` || find %(path)s -perm -g-x -exec chmod g+x '{}' \;" % locals()) #so effective can execute code
#    api.sudo("chmod g+s `find %(path)s -type d` || find %(path)s -type d -exec chmod g+s '{}' \;" % locals()) # so new files will keep same group
#    api.sudo("chmod g+s `find %(path)s -type d` || find %(path)s -type d -exec chmod g+s '{}' \;" % locals()) # so new files will keep same group
    
    for cache in [bc, dl, bc]:
        #HACK Have to deal with a shared cache. maybe need some kind of group
        api.sudo('mkdir -p %(cache)s && chown -R %(buildout)s:%(buildoutgroup)s %(cache)s && '
                 ' chmod -R u+rw,a+r %(cache)s ' % locals())

    #api.sudo('sudo -u $(effectiveuser) sh -c "export HOME=~$(effectiveuser) && cd $(install_dir) && bin/buildout -c $(hostout_file)"')

#    sudo('chmod 600 .installed.cfg')
#    sudo('find $(install_dir)  -type d -name var -exec chown -R $(effectiveuser) \{\} \;')
#    sudo('find $(install_dir)  -type d -name LC_MESSAGES -exec chown -R $(effectiveuser) \{\} \;')
#    sudo('find $(install_dir)  -name runzope -exec chown $(effectiveuser) \{\} \;')


def bootstrap_users():
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
        append(key, '~%s/.ssh/authorized_keys' % owner, use_sudo=True)
        api.sudo("chown -R %(owner)s ~%(owner)s/.ssh" % locals() )

@buildoutuser
def bootstrap_buildout():
    """ Create an initialised buildout directory """
    # bootstrap assumes that correct python is already installed
    path = api.env.path
    buildout = api.env['buildout-user']
    buildoutgroup = api.env['buildout-group']
    api.run('chown %(buildout)s:%(buildoutgroup)s %(path)s'%locals())

    buildoutcache = api.env['buildout-cache']
    api.run('mkdir -p %s/eggs' % buildoutcache)
    api.run('mkdir -p %s/downloads/dist' % buildoutcache)
    api.run('mkdir -p %s/extends' % buildoutcache)
    #api.run('chown -R %s:%s %s' % (buildout, buildoutgroup, buildoutcache))

    bootstrap = resource_filename(__name__, 'bootstrap.py')
    with cd(path):
        api.put(bootstrap, '%s/bootstrap.py' % path)
    
        # put in simplest buildout to get bootstrap to run
        api.run('echo "[buildout]" > buildout.cfg')

        python = api.env.get('python')
        if not python or python == 'buildout':

            version = api.env['python-version']
            major = '.'.join(version.split('.')[:2])
            python = "python%s" % major
    
        api.run('%s bootstrap.py --distribute' % python)

def bootstrap_buildout_ubuntu():
    
    api.sudo('apt-get update')
    
    api.sudo('apt-get -yq install '
             'build-essential ')
    
    api.sudo('apt-get -yq install '
             'python-dev ')
    
    api.env.hostout.bootstrap_buildout()

def bootstrap_python_buildout():
    "Install python from source via buildout"
    
    #TODO: need a better way to install from source that doesn't need svn or python
    
    path = api.env.path

    BUILDOUT = """
[buildout]
extends =
      src/base.cfg
      src/readline.cfg
      src/libjpeg.cfg
      src/python%(majorshort)s.cfg
      src/links.cfg

parts =
      ${buildout:base-parts}
      ${buildout:readline-parts}
      ${buildout:libjpeg-parts}
      ${buildout:python%(majorshort)s-parts}
      ${buildout:links-parts}

# ucs4 is needed as lots of eggs like lxml are also compiled with ucs4 since most linux distros compile with this      
[python-%(major)s-build:default]
extra_options +=
    --enable-unicode=ucs4
      
"""
    
    hostout = api.env.hostout
    hostout = api.env.get('hostout')
    buildout = api.env['buildout-user']
    effective = api.env['effective-user']
    buildoutgroup = api.env['buildout-group']

    #hostout.setupusers()
    api.sudo('mkdir -p %(path)s' % locals())
    hostout.setowners()

    version = api.env['python-version']
    major = '.'.join(version.split('.')[:2])
    majorshort = major.replace('.','')
    api.sudo('mkdir -p /var/buildout-python')
    with cd('/var/buildout-python'):
        #api.sudo('wget http://www.python.org/ftp/python/%(major)s/Python-%(major)s.tgz'%locals())
        #api.sudo('tar xfz Python-%(major)s.tgz;cd Python-%(major)s;./configure;make;make install'%locals())

        api.sudo('svn co http://svn.plone.org/svn/collective/buildout/python/')
        with cd('python'):
            api.sudo('curl -O http://python-distribute.org/distribute_setup.py')
            api.sudo('python distribute_setup.py')
            api.sudo('python bootstrap.py --distribute')
            append(BUILDOUT%locals(), 'buildout.cfg', use_sudo=True)
            api.sudo('bin/buildout')
    api.env['python'] = "source /var/buildout-python/python/python-%(major)s/bin/activate; python "
        
    #ensure bootstrap files have correct owners
    hostout.setowners()

def bootstrap_python():
    version = api.env['python-version']
    major = '.'.join(version.split('.')[:2])
    majorshort = major.replace('.','')
    d = dict(version=version)
    
    
    with cd('/tmp'):
        api.run('curl http://python.org/ftp/python/%(version)s/Python-%(version)s.tgz > Python-%(version)s.tgz'%d)
        api.run('tar xzf Python-%(version)s.tgz'%d)
        with cd('Python-%(version)s'%d):
#            api.run("sed 's/#readline/readline/' Modules/Setup.dist > TMPFILE && mv TMPFILE Modules/Setup.dist")
#            api.run("sed 's/#_socket/_socket/' Modules/Setup.dist > TMPFILE && mv TMPFILE Modules/Setup.dist")
            
            api.run('./configure  --enable-unicode=ucs4 --with-threads --with-readline --with-dbm --with-zlib --with-ssl --with-bz2')
            api.run('make')
            api.sudo('make altinstall')
        api.run("rm -rf /tmp/Python-%(version)s"%d)



def bootstrap_python_ubuntu():
    """Update ubuntu with build tools, python and bootstrap buildout"""
    hostout = api.env.get('hostout')
    path = api.env.path
     
    
    version = api.env['python-version']
    major = '.'.join(version.split('.')[:2])
    
    
    
    
    api.sudo('apt-get update')
    
    #Install and Update Dependencies
    

    #contrib.files.append(apt_source, '/etc/apt/source.list', use_sudo=True)
    api.sudo('apt-get -yq update ')
    api.sudo('apt-get -yq install '
             'build-essential '
#             'python-libxml2 '
#             'python-elementtree '
#             'python-celementtree '
             'ncurses-dev '
             'libncurses5-dev '
# needed for lxml on lucid
             'libz-dev '
             'libbz2-dev '
             'libxp-dev '
             'libreadline5 '
             'libreadline5-dev '
             'libssl-dev '
             'curl '
             #'openssl '
             #'openssl-dev '
             )

    try:
        api.sudo('apt-get -yq install python%(major)s python%(major)s-dev '%locals())
    except:
        hostout.bootstrap_python()

    #api.sudo('apt-get -yq update; apt-get dist-upgrade')

#    api.sudo('apt-get install python2.4=2.4.6-1ubuntu3.2.9.10.1 python2.4-dbg=2.4.6-1ubuntu3.2.9.10.1 \
# python2.4-dev=2.4.6-1ubuntu3.2.9.10.1 python2.4-doc=2.4.6-1ubuntu3.2.9.10.1 \
# python2.4-minimal=2.4.6-1ubuntu3.2.9.10.1')
    #wget http://mirror.aarnet.edu.au/pub/ubuntu/archive/pool/main/p/python2.4/python2.4-minimal_2.4.6-1ubuntu3.2.9.10.1_i386.deb -O python2.4-minimal.deb
    #wget http://mirror.aarnet.edu.au/pub/ubuntu/archive/pool/main/p/python2.4/python2.4_2.4.6-1ubuntu3.2.9.10.1_i386.deb -O python2.4.deb
    #wget http://mirror.aarnet.edu.au/pub/ubuntu/archive/pool/main/p/python2.4/python2.4-dev_2.4.6-1ubuntu3.2.9.10.1_i386.deb -O python2.4-dev.deb
    #sudo dpkg -i python2.4-minimal.deb python2.4.deb python2.4-dev.deb
    #rm python2.4-minimal.deb python2.4.deb python2.4-dev.deb

    # python-profiler?
    
def bootstrap_python_redhat():
    hostout = api.env.get('hostout')
    #Install and Update Dependencies
    user = hostout.options['user']

    hostout.bootstrap_allowsudo()

    # Redhat/centos don't have Python 2.6 or 2.7 in stock yum repos, use EPEL.
    # Could also use RPMforge repo: http://dag.wieers.com/rpm/FAQ.php#B
    api.sudo("rpm -Uvh --force http://download.fedora.redhat.com/pub/epel/5/i386/epel-release-5-4.noarch.rpm")


    version = api.env['python-version']
    python_versioned = 'python' + ''.join(version.split('.')[:2])

    api.sudo('yum -y install gcc gcc-c++ ')

    api.sudo('yum -y install ' +
             python_versioned + ' ' +
             python_versioned + '-devel ' + 
             'python-setuptools '
             'libxml2-python '
             'python-elementtree '
             'ncurses-devel '
             'zlib zlib-devel '
             'readline-devel '
             'bzip2-devel '
             'openssl openssl-dev '
             )

#optional stuff
#    api.sudo('yum -y install ' +
#             'python-imaging '
#             'libjpeg-devel '
#             'freetype-devel '
#             'lynx '
#             'openssl-devel '
#             'libjpeg-devel '
#            'openssl openssl-devel '
#            'libjpeg libjpeg-devel '
#            'libpng libpng-devel '
#            'libxml2 libxml2-devel '
#            'libxslt libxslt-devel ')



def detecthostos():
    #http://wiki.linuxquestions.org/wiki/Find_out_which_linux_distribution_a_system_belongs_to
    hostos = api.run(
        "([ -e /etc/SuSE-release ] && echo SuSE) || "
                "([ -e /etc/redhat-release ] && echo redhat) || "
                "([ -e /etc/fedora-release ] && echo fedora) || "
                "(lsb_release -is) || "
                "([ -e /etc/slackware-version ] && echo slackware)"
               )
    if hostos:
        hostos = hostos.lower().strip()
    print "Detected Hostos = %s" % hostos
    api.env['hostos'] = hostos
    return hostos


def bootstrap_allowsudo():
    """Allow any sudo without tty"""
    hostout = api.env.get('hostout')
    user = hostout.options['user']

    try:
        api.sudo("egrep \"^\%odas\ \ ALL\=\(ALL\)\ ALL\" \"/etc/sudoers\"",pty=True)
    except:
        api.sudo("echo '%odas  ALL=(ALL) ALL' >> /etc/sudoers",pty=True)

    try:
        api.sudo("egrep \"^Defaults\:\%%%(user)s\ \!requiretty\" \"/etc/sudoers\"" % dict(user=user), pty=True)
    except:
        api.sudo("echo 'Defaults:%%%(user)s !requiretty' >> /etc/sudoers" % dict(user=user), pty=True)
    



#def initcommand(cmd):
#    if cmd in ['uploadeggs','uploadbuildout','buildout','run']:
#        api.env.user = api.env.hostout.options['buildout-user']
#    else:
#        api.env.user = api.env.hostout.options['user']
#    key_filename = api.env.get('identity-file')
#    if key_filename and os.path.exists(key_filename):
#        api.env.key_filename = key_filename


