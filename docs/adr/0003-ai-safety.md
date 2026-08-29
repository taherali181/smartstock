# ADR 0003: RAG and action safety

Status: accepted

Operational facts come from permission-checked typed tools; document answers use tenant/ACL-filtered hybrid retrieval. Retrieved text is untrusted. Model output cannot call write tools. Requested writes become inert, expiring proposals bound to evidence and entity versions. Approval reauthorizes, revalidates, and invokes the normal domain command.

Champion models are administrator-promoted only after pinned, reproducible evaluation releases. Fallback requires equivalent security and capability profiles; external model APIs are disabled.
