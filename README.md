# fustercluck
All the stuff that makes my fun cluster do the stuff it do.


## Key Components

1. Controller pi (3b+, cluster0 in `cluster.yml`)
2. SSH & Keys
3. [Cloudalchemy's node_exporter role](https://github.com/cloudalchemy/ansible-node-exporter)[^1] `ansible-galaxy install cloudalchemy.node-exporter`
4. 

## Steps

1. Flash Pi SD 
	1. Touch `ssh` in `boot` vol (/Volumes/boot on MacOS)
3. `ssh` to Controller using default username/password
4. Copy `pi` user keys to controller
5. Run `playbooks/cluster_setup.yml` against the controller RaspberryPi. 
	* `ansible-playbook -i cluster.yml -limit main  --private-key [key file] -u pi playbooks/new_host_init.yml`
6. Run playbooks for appropriate role configurations

---

## Footnotes
[1]: The CloudAlchemy role, if run from a Mac, requires disabling export OBJC_DISABLE_INITIALIZE_FORK_SAFETY=YES (more detail [here](https://github.com/cloudalchemy/ansible-node-exporter/issues/54) and [here](http://sealiesoftware.com/blog/archive/2017/6/5/Objective-C_and_fork_in_macOS_1013.html)) and requires `gnu-tar` (`brew install gnu-tar`)