#!/usr/bin/python
# -*- coding: utf-8 -*-

DOCUMENTATION = r'''
---
module: fleet_vault_update
short_description: Controller-side encrypted fleet vault reconciliation
description:
  - This module is paired with a controller-side action plugin of the same name.
  - The action plugin performs all Vault operations on the Ansible controller.
options:
  path:
    type: path
    required: true
  vault_password_file:
    type: path
    required: true
  site_id:
    type: str
    required: true
  site_update:
    type: dict
    default: {}
  migrate_flat_pve:
    type: bool
    default: true
author:
  - DigitalHouses
'''

from ansible.module_utils.basic import AnsibleModule


def main():
    module = AnsibleModule(
        argument_spec=dict(
            path=dict(type="path", required=True),
            vault_password_file=dict(type="path", required=True, no_log=True),
            site_id=dict(type="str", required=True),
            site_update=dict(type="dict", default={}, no_log=True),
            migrate_flat_pve=dict(type="bool", default=True),
        ),
        supports_check_mode=True,
    )

    module.fail_json(
        msg=(
            "fleet_vault_update controller action plugin was not loaded. "
            "Check ansible.cfg action_plugins configuration."
        )
    )


if __name__ == "__main__":
    main()
