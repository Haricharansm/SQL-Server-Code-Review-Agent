"use strict";

const form = document.querySelector("#review-form");
const editor = document.querySelector("#sql");
const sourceOptions = form.querySelectorAll("[name=source]");

function syncSource() {
  const editorActive = form.elements.source.value === "editor";
  document.querySelector("#editor-fields").hidden = !editorActive;
  document.querySelector("#database-fields").hidden = editorActive;
  editor.disabled = !editorActive;
  form.elements.module.disabled = editorActive;
  form.elements.spid.disabled = editorActive;
}

sourceOptions.forEach((option) => option.addEventListener("change", syncSource));
syncSource();

document.querySelector("#load-example").addEventListener("click", () => {
  if (editor.value.trim() && !window.confirm("Replace the current editor content with the example?")) {
    return;
  }
  editor.value = [
    "CREATE OR ALTER PROCEDURE dbo.usp_GetDeals",
    "    @Year int",
    "AS",
    "BEGIN",
    "    SET NOCOUNT ON;",
    "",
    "    SELECT DISTINCT d.*",
    "    FROM dbo.Deal AS d",
    "    INNER JOIN dbo.Account AS a ON a.Id = d.AccountId",
    "    WHERE YEAR(d.CreatedAt) = @Year;",
    "END;",
  ].join("\n");
  editor.focus();
});

const runButton = document.querySelector("#submit-review");
const runStatus = document.querySelector("#run-status");

form.addEventListener("submit", () => {
  runButton.disabled = true;
  runStatus.textContent = "Review running…";
});
window.addEventListener("pageshow", () => {
  runButton.disabled = false;
  runStatus.textContent = "";
});

const reportData = document.querySelector("#report-data");
if (reportData) {
  document.querySelector("#download-report").addEventListener("click", () => {
    const blob = new Blob([JSON.parse(reportData.textContent)], { type: "text/markdown;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = "sql-review.md";
    document.body.append(link);
    link.click();
    link.remove();
    window.setTimeout(() => URL.revokeObjectURL(url), 1000);
  });
}

const copySql = document.querySelector("#copy-sql");
if (copySql) {
  copySql.addEventListener("click", async () => {
    const status = document.querySelector("#copy-status");
    try {
      await navigator.clipboard.writeText(document.querySelector("#candidate-sql").textContent);
      status.textContent = "SQL copied.";
    } catch {
      status.textContent = "Clipboard unavailable. Select and copy the SQL.";
    }
  });
}

const completed = document.querySelector("#results[data-complete]");
if (completed) {
  completed.focus({ preventScroll: true });
  if (window.matchMedia("(max-width: 790px)").matches) {
    completed.scrollIntoView({ block: "start" });
  }
}
