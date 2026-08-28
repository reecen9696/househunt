// Kept in chrome.storage.sync so the address and password follow the profile
// rather than being baked into the extension.
const FIELDS = ["baseUrl", "user", "password"];
const DEFAULTS = { baseUrl: "http://127.0.0.1:8765", user: "reece", password: "" };

chrome.storage.sync.get(DEFAULTS).then((cfg) => {
  for (const f of FIELDS) document.getElementById(f).value = cfg[f] ?? "";
});

document.getElementById("save").addEventListener("click", async () => {
  const values = {};
  for (const f of FIELDS) values[f] = document.getElementById(f).value.trim();
  await chrome.storage.sync.set(values);
  const status = document.getElementById("status");
  status.textContent = "Saved";
  setTimeout(() => { status.textContent = ""; }, 2000);
});
