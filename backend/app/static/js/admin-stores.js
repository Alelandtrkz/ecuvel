document.addEventListener("DOMContentLoaded", () => {
  const dialog = document.querySelector("[data-store-dialog]");
  const opener = document.querySelector("[data-store-dialog-open]");
  const closers = dialog?.querySelectorAll("[data-store-dialog-close]") || [];
  const issueList = dialog?.querySelector("[data-document-issue-list]");
  const issueTemplate = dialog?.querySelector("[data-document-issue-template]");
  const addIssueButton = dialog?.querySelector("[data-document-issue-add]");
  let submitting = false;

  const renumberIssues = () => {
    issueList?.querySelectorAll("[data-document-issue-row]").forEach((row, index) => {
      const legend = row.querySelector("legend");
      if (legend) legend.textContent = `Corrección documental ${index + 1}`;
    });
  };

  const removeIssue = (button) => {
    button.closest("[data-document-issue-row]")?.remove();
    renumberIssues();
    addIssueButton?.focus();
  };

  issueList?.addEventListener("click", (event) => {
    const removeButton = event.target.closest("[data-document-issue-remove]");
    if (removeButton) removeIssue(removeButton);
  });

  addIssueButton?.addEventListener("click", () => {
    if (!issueList || !issueTemplate) return;
    const maxIssues = Number.parseInt(issueList.dataset.maxIssues || "20", 10);
    if (issueList.querySelectorAll("[data-document-issue-row]").length >= maxIssues) return;
    const fragment = issueTemplate.content.cloneNode(true);
    issueList.appendChild(fragment);
    renumberIssues();
    issueList.querySelector("[data-document-issue-row]:last-child select")?.focus();
    if (window.lucide) window.lucide.createIcons();
  });

  const close = () => {
    if (!dialog?.open || submitting) return;
    dialog.close();
    opener?.focus();
  };
  opener?.addEventListener("click", () => {
    if (!dialog) return;
    dialog.showModal();
    dialog.querySelector("input, select, textarea, button")?.focus();
  });
  closers.forEach((button) => button.addEventListener("click", close));
  dialog?.addEventListener("click", (event) => {
    if (event.target === dialog) close();
  });
  dialog?.addEventListener("cancel", (event) => {
    event.preventDefault();
    close();
  });
  dialog?.querySelector("form")?.addEventListener("submit", () => {
    submitting = true;
  });
});
