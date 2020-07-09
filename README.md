# fustercluck
All the stuff that makes my fun cluster do the stuff it do.


## Key Components

1. Controller pi (3b+, cluster0 in `inventory/cluster.yml')
2. ClusterHat
3. 4x Pi ZeroWs
5. SSH & Keys
6. Ansible

## Steps

1. Flash Controller Pi SD 
    * [CBRIDGE - Lite Controller Buster Image](http://dist.8086.net/clusterctrl/buster/2020-02-13/ClusterCTRL-2020-02-13-lite-1-CBRIDGE.zip)
2. Configure base image for [USBBOOT of ZeroWs](https://8086.support/content/23/97/en/how-do-i-boot-pi-zeros-without-sd-cards-cluster-hat_cluster-ctrl.html)
3. Boot ZeroWs, all have bridge IPs on local LAN
    * This is to allow me to more easily accomplish the following:
        * Run Ansible Playbooks from my PCs rather than require the cluster controller to be the Ansible host.
        * `HomeBridge` requires full access to the local LAN; I'd prefer to run this in a container on the Controller Pi. 
4. Create a *new* user and SSH keys on Controller and Worker Pis
5. Create Inventory file (`inventory/cluster.yml`)
