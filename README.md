# fustercluck
All the stuff that makes my fun cluster do the stuff it do.


## Key Components

1. Controller pi (3b+, cluster0 in `cluster.yml`)
2. ClusterHat
3. 4x Pi ZeroWs
5. SSH & Keys
6. Ansible

## Steps

1. Flash Controller Pi SD 
    * [CBRIDGE - Lite Controller Buster Image](http://dist.8086.net/clusterctrl/buster/2020-02-13/ClusterCTRL-2020-02-13-lite-1-CBRIDGE.zip)
	1. Touch `ssh` in `boot` vol (/Volumes/boot on MacOS)
2. Boot controller rPi from SD
3. `ssh` to Controller using default username/password
4. Copy `pi` user keys to controller
5. Run `playbooks/cluster_setup.yml` against the controller RaspberryPi. 
	* `ansible-playbook -i cluster.yml -limit main  --private-key [priv key file] -u pi playbooks/cluster_setup.yml -K`
6. Boot ZeroWs, all have bridge IPs on local LAN 
4. Copy `pi` user key to blades
5. Setup the blades
	* `ansible-playbook -i cluster.yml --limit workers --private-key ~/.ssh/pi_id_ecdsa -u pi playbooks/cluster_setup.yml  -K --tags workers`
6. Change user passwords on all rPis (Controller and Workers)
