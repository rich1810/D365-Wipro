# Intake Registry

Every intake batch gets a stable **`INTK-####`** id, assigned by the intake agent
(Phase 1) when it processes an intake issue. This id makes every downstream artifact
traceable to the intake that drove it, independent of the (sequential) REQ/FEAT ids:

- each `specs/intakes/INTK-####/requirements/INTK-####-REQ-###.md` carries
  `intake_batch: INTK-####` back to a row below;
- each feature derives its intake trail through its member requirements rather
  than storing a duplicate intake-batch field.

Ids are zero-padded and sequential (`conventions.yml` `intake_batch_format`). A friendly
id (not the folder path) is used so that multiple intakes on the same day — or from
different submitters — never collide.

| INTK | Folder | Intake issue | Date | Submitter | REQ range | Status |
|------|--------|--------------|------|-----------|-----------|--------|
