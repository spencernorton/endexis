# Legal, Trademark, and Interoperability Notice

## Independent Open-Source Interoperability Software

**Endexis** (`endexis`, *EHR Neural Data Extraction, Inquiry & XML Intelligence System*) is an independent open-source software toolkit and neural compiler developed by the **Endexis Contributors**.

**Non-Affiliation Disclaimer:** Endexis and its contributors are **not** affiliated with, endorsed by, sponsored by, or certified by **Oracle Corporation**, **Cerner Corporation**, **Oracle Health**, or the **Eclipse Foundation**.

## Nominative Fair Use of Third-Party Trademarks

Any references in documentation, schemas, or code comments to **Oracle®**, **Cerner®**, **Cerner Millennium®**, **Discern Analytics™**, **Discern Explorer™**, **PowerChart®**, **PowerForms™**, **Interactive View (IView)™**, **HealtheIntent®**, or **Eclipse BIRT™** are made strictly for **nominative fair-use interoperability and compatibility description purposes** (identifying the target database schemas, XML report formats, and query languages with which Endexis interoperates). All third-party trademarks and registered trademarks are the property of their respective owners.

## Zero PHI & Tenant-Agnostic Design

1. **Zero Protected Health Information (PHI):** Endexis contains zero patient records, zero medical record numbers (MRNs), zero financial encounter numbers (FINs), and zero protected health information as defined under HIPAA (45 CFR § 164.514).
2. **Zero Client or Regional Tenant Data:** All catalogs, training corpora, and model weights are strictly tenant-agnostic. Generated Discern Analytics 2.0 (`.daquery` and `.dareport`) artifacts default to the placeholder owner group `TENANT_OWNER_GROUP` and require the user to supply their own target tenant's owner group (`--owner-group` or `--tenant-config`) at runtime when importing into an authorized environment.
