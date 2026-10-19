# Inventories

`inventories/` содержит **site-specific desired state** для всех объектов DigitalHouses.

Один repository обслуживает весь fleet:

```text
inventories/
├── amir/
├── kraft-office/
└── <site>/
```

Каталог site содержит только данные конкретного объекта. Общая логика не должна жить внутри `inventories/`.

## Contract

- reusable behavior → `roles/` или `plugins/modules/`;
- common lifecycle entrypoints → `playbooks/`;
- site facts/desired values → `inventories/<site>/`;
- credentials → namespaced data внутри единого tracked `secrets/fleet.vault.yml`; inventory содержит только references/desired state, но не credential material;
- plaintext secret files и Vault master password → никогда не Git;
- потеря runner не должна терять credentials: Git + master password являются DR source;
- новый типовой site не должен требовать копии роли или playbook.

Amir — первый pilot inventory, не шаблон с hard-coded assumptions.
