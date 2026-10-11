"use strict";
(() => {
  const body = document.body;
  const cases = Array.from(document.querySelectorAll(".case"));
  const mode = document.querySelector("#picture-mode");
  const filter = document.querySelector("#category");
  const storageKey = "palette-comparison:" + body.dataset.reportId;
  const storageNote = document.querySelector("#storage-note");
  let choices = Object.create(null);
  let storageOK = true;
  try {
    const value = JSON.parse(localStorage.getItem(storageKey) || "{}");
    if (value && typeof value === "object" && !Array.isArray(value)) {
      choices = Object.assign(Object.create(null), value);
    }
    // A rebuild can revoke an image. Export only choices still offered on this page.
    const current = Object.create(null);
    cases.forEach(section => {
      const choice = choices[section.dataset.case];
      if (Array.from(section.querySelectorAll("[data-choice]"))
        .some(button => button.dataset.choice === choice)) {
        current[section.dataset.case] = choice;
      }
    });
    choices = current;
    localStorage.setItem(storageKey, JSON.stringify(choices));
  } catch (_) {
    storageOK = false;
    storageNote.textContent = "此浏览器未开放本地存储；选择仅保留到本页关闭，可导出。";
  }
  function refresh(section) {
    const choice = choices[section.dataset.case];
    const selected = Array.from(section.querySelectorAll("[data-choice]"))
      .find(button => button.dataset.choice === choice);
    section.querySelectorAll("[data-choice]").forEach(button => {
      button.setAttribute("aria-pressed", String(button === selected));
    });
    let description = "尚未选择";
    if (selected) {
      const card = selected.closest(".card");
      description = card ? card.querySelector(mode.checked ? ".anonymous" : ".identity").textContent : selected.textContent;
    }
    section.querySelector(".selection-output").textContent = "本地选择：" + description;
  }
  function persist() {
    if (storageOK) {
      try { localStorage.setItem(storageKey, JSON.stringify(choices)); }
      catch (_) { storageNote.textContent = "本地存储失败；当前选择可导出。"; }
    }
  }
  mode.addEventListener("change", () => {
    body.classList.toggle("picture-only", mode.checked);
    cases.forEach(refresh);
  });
  filter.addEventListener("change", () => {
    cases.forEach(section => { section.hidden = !!filter.value && section.dataset.category !== filter.value; });
  });
  function shuffle(values) {
    for (let i = values.length - 1; i > 0; i--) {
      const j = Math.floor(Math.random() * (i + 1));
      [values[i], values[j]] = [values[j], values[i]];
    }
    return values;
  }
  document.querySelector("#shuffle").addEventListener("click", () => {
    mode.checked = true;
    mode.dispatchEvent(new Event("change"));
    cases.forEach(section => {
      const grid = section.querySelector(".current-grid");
      shuffle(Array.from(grid.querySelectorAll(".solution"))).forEach((card, index) => {
        card.querySelector(".anonymous").textContent = "候选 " + (index + 1);
        grid.appendChild(card);
      });
      section.querySelectorAll(".baseline-grid").forEach(history => {
        shuffle(Array.from(history.children)).forEach((card, index) => {
          card.querySelector(".anonymous").textContent = "历史参考 " + (index + 1);
          history.appendChild(card);
        });
      });
      refresh(section);
    });
  });
  cases.forEach(section => {
    section.querySelectorAll("[data-choice]").forEach(button => {
      button.addEventListener("click", () => {
        choices[section.dataset.case] = button.dataset.choice;
        persist(); refresh(section);
      });
    });
    refresh(section);
  });
  document.querySelector("#clear-choices").addEventListener("click", () => {
    choices = Object.create(null); persist(); cases.forEach(refresh);
  });
  document.querySelector("#export-choices").addEventListener("click", () => {
    const payload = {schema_version:"1.0", report_id:body.dataset.reportId,
      exported_at:new Date().toISOString(), kind:"local_user_preference", choices,
      generation_authorized:false,
      sources:cases.map(section => ({report_case_id:section.dataset.case,
        case_id:section.dataset.sourceCase, round_number:Number(section.dataset.round) || null,
        result_sha256:section.dataset.resultSha256 || null}))};
    const link = document.createElement("a");
    const url = URL.createObjectURL(new Blob([JSON.stringify(payload, null, 2)], {type:"application/json"}));
    link.href = url; link.download = "本地偏好选择.json"; link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  });
  const viewer = document.querySelector("#viewer");
  const panes = viewer.querySelector(".viewer-panes");
  const mainImage = viewer.querySelector("#viewer-image");
  const referenceImage = viewer.querySelector("#viewer-reference");
  const pairButton = viewer.querySelector("#compare");
  const switchButton = viewer.querySelector("#switch-image");
  let activeImage = "", originalImage = "", activeCaption = "当前图", showingOriginal = false;
  document.querySelectorAll("[data-image]").forEach(button => {
    button.addEventListener("click", () => {
      activeImage = button.dataset.image;
      originalImage = button.closest(".case").dataset.original;
      showingOriginal = false;
      mainImage.src = activeImage;
      referenceImage.removeAttribute("src");
      panes.classList.remove("paired");
      pairButton.setAttribute("aria-pressed", "false");
      pairButton.disabled = switchButton.disabled = !originalImage || originalImage === activeImage;
      switchButton.textContent = "切换原图";
      const card = button.closest(".card");
      activeCaption = card.querySelector(".status").textContent;
      viewer.querySelector("#viewer-caption").textContent = activeCaption;
      viewer.querySelector("h2").textContent = card.querySelector(mode.checked ? ".anonymous" : ".identity").textContent;
      viewer.showModal();
    });
  });
  pairButton.addEventListener("click", () => {
    const paired = panes.classList.toggle("paired");
    pairButton.setAttribute("aria-pressed", String(paired));
    if (paired) { referenceImage.src = originalImage; mainImage.src = activeImage; showingOriginal = false; }
    switchButton.textContent = "切换原图";
    viewer.querySelector("#viewer-caption").textContent = activeCaption;
  });
  switchButton.addEventListener("click", () => {
    panes.classList.remove("paired"); pairButton.setAttribute("aria-pressed", "false");
    showingOriginal = !showingOriginal;
    mainImage.src = showingOriginal ? originalImage : activeImage;
    switchButton.textContent = showingOriginal ? "切回当前图" : "切换原图";
    viewer.querySelector("#viewer-caption").textContent = showingOriginal ? "原图" : activeCaption;
  });
  viewer.querySelector("#close-viewer").addEventListener("click", () => viewer.close());
  viewer.addEventListener("click", event => { if (event.target === viewer) viewer.close(); });
})();
