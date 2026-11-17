/*
Illustrative SQL Server procedure. Adapt table and column names in a test database.
Assumes dbo.Deal.DealId is a positive unique bigint and dbo.Account.Id is unique.
The procedure returns a new, narrower summary contract with keyset pagination.
Preserve existing wide-output callers by deploying a distinct procedure name.
Defaults return the first page after DealId 0. Pass the last returned ID for the next page.
For a consistent multi-page snapshot, use an appropriate snapshot strategy at the caller.
*/
CREATE OR ALTER PROCEDURE dbo.usp_GetDealSummariesPage
    @Stage nvarchar(100),
    @BusinessUnit nvarchar(100),
    @AfterDealId bigint = 0,
    @PageSize int = 100
AS
BEGIN
    SET NOCOUNT ON;

    IF @Stage IS NULL OR @BusinessUnit IS NULL OR @AfterDealId IS NULL
       OR @AfterDealId < 0 OR @PageSize IS NULL OR @PageSize NOT BETWEEN 1 AND 500
        THROW 50001, 'Specify valid filters, cursor and page size from 1 to 500.', 1;

    SELECT TOP (@PageSize)
        d.DealId, d.DealName, d.Stage, d.BusinessUnit, a.AccountName
    FROM dbo.Deal AS d
    LEFT JOIN dbo.Account AS a ON a.Id = d.AccountId
    WHERE d.Stage = @Stage AND d.BusinessUnit = @BusinessUnit
      AND d.DealId > @AfterDealId
    ORDER BY d.DealId;
END;
GO
