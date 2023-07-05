# fustercluck
All the stuff that makes my fun cluster do the stuff it do.


## Key Components

1. One or more raspberries pi per `inventory.yml`
2. SSH & Keys
3. [Cloudalchemy's node_exporter role](https://github.com/cloudalchemy/ansible-node-exporter)[^1] `ansible-galaxy install cloudalchemy.node-exporter`
4. [Jeff Geerling's NFS Role](https://github.com/geerlingguy/ansible-role-nfs)

## Requirements
	1. `community.general.snap`


## Steps
	
### If adding a new Raspberry Pi node

1. Flash Pi SD 
	1. Touch `ssh` in `boot` vol (/Volumes/boot on MacOS)
3. `ssh` to  new host using default username/password
4. Copy `pi` user keys to controller
5. Run `new_host_init.yml` against the new host RaspberryPi. 
	* `ansible-playbook --private-key [key file] -u pi playbooks/new_host_init.yml`
6. Run playbooks for appropriate node types

### If adding a new host in general

1. Create `ansible` user and copy `ansible` keys to new host
2. Run `new_host_init.yml` against the host as the `ansible` user. 
	* `ansible-playbook --private-key [key file] -u pi playbooks/new_host_init.yml`
	
--- 

## Footnotes
[1]: The CloudAlchemy role, if run from a Mac, requires `export OBJC_DISABLE_INITIALIZE_FORK_SAFETY=YES` (more detail [here](https://github.com/cloudalchemy/ansible-node-exporter/issues/54) and [here](http://sealiesoftware.com/blog/archive/2017/6/5/Objective-C_and_fork_in_macOS_1013.html)) and requires `gnu-tar` (`brew install gnu-tar`)