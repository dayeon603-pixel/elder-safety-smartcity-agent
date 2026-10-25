Add `NNNN_short_name.sql` (NNNN >= 0002, lowercase snake_case). Files apply once, in order,
each inside one transaction; applied files are checksummed in `schema_version`.
Never edit an applied file; add a new one.
