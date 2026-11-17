-- Queries use ODBC positional parameters. The Python runner binds all values.
-- Run these queries with a principal authorized to view the relevant metadata.

-- @query object
SELECT o.object_id, o.type_desc, m.definition
FROM sys.objects AS o
JOIN sys.schemas AS s ON s.schema_id = o.schema_id
JOIN sys.sql_modules AS m ON m.object_id = o.object_id
WHERE s.name = ? AND o.name = ? AND o.type IN ('P', 'V');

-- @query dependencies
;WITH refs AS (
    SELECT d.referenced_id, d.referenced_server_name, d.referenced_database_name,
           d.referenced_schema_name, d.referenced_entity_name, 0 AS depth,
           CAST('/' + CONVERT(varchar(11), d.referencing_id) + '/' AS varchar(max)) AS path
    FROM sys.sql_expression_dependencies AS d
    WHERE d.referencing_id = ? AND d.referenced_class = 1
    UNION ALL
    SELECT d.referenced_id, d.referenced_server_name, d.referenced_database_name,
           d.referenced_schema_name, d.referenced_entity_name, r.depth + 1,
           CAST(r.path + CONVERT(varchar(11), d.referencing_id) + '/' AS varchar(max))
    FROM refs AS r
    JOIN sys.views AS v ON v.object_id = r.referenced_id
    JOIN sys.sql_expression_dependencies AS d ON d.referencing_id = v.object_id
    WHERE d.referenced_class = 1 AND r.depth < 8
      AND CHARINDEX('/' + CONVERT(varchar(11), v.object_id) + '/', r.path) = 0
)
SELECT DISTINCT r.referenced_id AS object_id, r.referenced_server_name AS server_name,
       r.referenced_database_name AS database_name,
       COALESCE(s.name, r.referenced_schema_name) AS schema_name,
       r.referenced_entity_name AS object_name, o.type_desc AS object_type,
       syn.base_object_name AS synonym_target,
       PARSENAME(syn.base_object_name, 4) AS synonym_server,
       CAST(CASE WHEN ext.object_id IS NULL THEN 0 ELSE 1 END AS bit) AS external_table
FROM refs AS r
LEFT JOIN sys.objects AS o ON o.object_id = r.referenced_id
LEFT JOIN sys.schemas AS s ON s.schema_id = o.schema_id
LEFT JOIN sys.synonyms AS syn ON syn.object_id = o.object_id
LEFT JOIN sys.external_tables AS ext ON ext.object_id = o.object_id;

-- @query columns
SELECT name AS column_name, TYPE_NAME(system_type_id) AS base_type, max_length AS max_bytes
FROM sys.columns
WHERE object_id = ?
  AND (TYPE_NAME(system_type_id) IN ('text', 'ntext', 'image', 'xml')
       OR (TYPE_NAME(system_type_id) IN ('varchar', 'nvarchar', 'char', 'nchar', 'varbinary', 'binary')
           AND (max_length = -1 OR max_length >= 1000)));

-- @query indexes
SELECT i.name AS index_name, i.type_desc, i.is_unique, i.is_disabled,
       i.has_filter, i.filter_definition, c.name AS column_name,
       ic.key_ordinal, ic.is_included_column
FROM sys.indexes AS i
JOIN sys.index_columns AS ic ON ic.object_id = i.object_id AND ic.index_id = i.index_id
JOIN sys.columns AS c ON c.object_id = ic.object_id AND c.column_id = ic.column_id
WHERE i.object_id = ? AND i.index_id > 0 AND i.is_hypothetical = 0
ORDER BY i.index_id, ic.key_ordinal, ic.index_column_id;

-- @query statistics
SELECT s.name AS statistics_name, c.name AS leading_column, p.last_updated,
       p.rows, p.rows_sampled, p.modification_counter
FROM sys.stats AS s
JOIN sys.stats_columns AS sc
  ON sc.object_id = s.object_id AND sc.stats_id = s.stats_id AND sc.stats_column_id = 1
JOIN sys.columns AS c ON c.object_id = sc.object_id AND c.column_id = sc.column_id
OUTER APPLY sys.dm_db_stats_properties(s.object_id, s.stats_id) AS p
WHERE s.object_id = ?;

-- @query request
SELECT r.session_id, r.request_id, r.status, r.wait_type, r.wait_time AS wait_ms,
       r.blocking_session_id, r.wait_resource, r.total_elapsed_time AS elapsed_ms,
       r.cpu_time AS cpu_ms, r.logical_reads, r.row_count
FROM sys.dm_exec_requests AS r
WHERE r.session_id = ? AND r.database_id = DB_ID() AND r.session_id <> @@SPID;

-- @query history
SELECT TOP (20) q.query_id, p.plan_id, rs.execution_type_desc, rs.count_executions,
       rs.avg_duration AS avg_duration_us, rs.avg_cpu_time AS avg_cpu_us,
       rs.avg_logical_io_reads AS avg_logical_pages,
       CONVERT(varchar(48), rs.last_execution_time, 127) AS last_execution_time
FROM sys.query_store_query AS q
JOIN sys.query_store_plan AS p ON p.query_id = q.query_id
JOIN sys.query_store_runtime_stats AS rs ON rs.plan_id = p.plan_id
WHERE q.object_id = ?
ORDER BY rs.last_execution_time DESC, q.query_id, p.plan_id;
