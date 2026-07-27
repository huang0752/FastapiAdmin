-- PostgreSQL tenant Row-Level Security template.
--
-- This file is intentionally opt-in. Apply one table at a time only after every
-- request and worker transaction sets app.tenant_id with SET LOCAL. Run the
-- statements as the table owner; the application role must not have BYPASSRLS.

BEGIN;

-- Example for a tenant-owned table. Repeat explicitly for each reviewed table;
-- do not generate policies for platform/global tables.
ALTER TABLE sys_ticket ENABLE ROW LEVEL SECURITY;
ALTER TABLE sys_ticket FORCE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS tenant_isolation ON sys_ticket;
CREATE POLICY tenant_isolation ON sys_ticket
    USING (
        tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::bigint
    )
    WITH CHECK (
        tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::bigint
    );

-- Verification examples. Keep the transaction open so SET LOCAL remains scoped
-- to this transaction and cannot leak through the connection pool.
SET LOCAL app.tenant_id = '1';
SELECT count(*) AS visible_rows_for_tenant_1 FROM sys_ticket;

ROLLBACK;
