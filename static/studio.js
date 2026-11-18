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
      await navigator.clipboard.writeText(document.querySelector("#candidate-sql").value);
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

const draftForm = document.querySelector("#draft-form");
if (draftForm) {
  draftForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    const candidate = document.querySelector("#candidate-sql").value;
    const status = document.querySelector("#draft-status");
    if (!candidate.trim()) {
      status.textContent = "Enter a complete candidate before creating a draft.";
      return;
    }
    const button = draftForm.querySelector("button");
    button.disabled = true;
    status.textContent = "Creating draft PR…";
    try {
      const body = new FormData(draftForm);
      body.set("candidate", candidate);
      const response = await fetch(draftForm.action, { method: "POST", body });
      const result = await response.json();
      if (!response.ok) {
        status.textContent = result.error;
        button.disabled = false;
        return;
      }
      const link = document.createElement("a");
      link.href = result.url;
      link.textContent = "Open draft PR on GitHub";
      status.replaceChildren(link);
    } catch {
      status.textContent = "Draft creation could not be confirmed. Check GitHub before retrying.";
      button.disabled = false;
    }
  });
}

const compareSql = document.querySelector("#compare-sql");
if (compareSql) {
  const diff = document.querySelector("#sql-diff");
  const candidate = document.querySelector("#candidate-sql");

  candidate.addEventListener("input", () => { diff.hidden = true; });
  compareSql.addEventListener("click", async () => {
    const snapshot = candidate.value;
    const body = new URLSearchParams({
      csrf: form.elements.csrf.value,
      original: document.querySelector("#original-sql").value,
      candidate: snapshot,
    });
    compareSql.disabled = true;
    try {
      const response = await fetch(compareSql.dataset.url, { method: "POST", body });
      const result = await response.json();
      diff.textContent = result.error || result.diff || "No SQL changes.";
    } catch {
      diff.textContent = "Could not compare the definitions. Try again.";
    } finally {
      diff.hidden = snapshot !== candidate.value;
      compareSql.disabled = false;
    }
  });
}
