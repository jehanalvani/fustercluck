Role Name
=========

My own grafana role. Built for personal use in Docker on a Raspberry Pi cluster. Simple handler to restart container when grafani.ini is changed. 


Requirements
------------

Ansible, Docker on host node. 


Role Variables
--------------

* `{{ nfs_server }}`: NFS server for data configuration persistence.
* `{{ grafana_plugins }}`: list of plugins to be installed
* `{{ grafana_data_nfs_path }}`, `{{ grafana_config_nfs_path }}`: path on NFS exporter to data, path to mount and deploy on host
* `{{ grafana_data_mnt_path }}`, `{{ grafana_config_mnt_path }}`: path on NFS exporter to config, path to mount and deploy on host
* `{{ grafana_smtp_username }}`, `{{ grafana_smtp_password }}`: vault encrypted username and password for SMTP

Dependencies
------------

A list of other roles hosted on Galaxy should go here, plus any details in regards to parameters that may need to be set for other roles, or variables that are used from other roles.

Example Playbook
----------------

Including an example of how to use your role (for instance, with variables passed in as parameters) is always nice for users too:

    - hosts: application
      roles:
         - role: grafana
           tags:
           	 - grafana
      

License
-------

BSD

Author Information
------------------

An optional section for the role authors to include contact information, or a website (HTML is not allowed).
