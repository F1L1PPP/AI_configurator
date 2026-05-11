// Shared client-side state for the fake site.
// localStorage acts as the "device" persisting between page loads.

const TOKEN_KEY = "ppg_token";
const VLAN_KEY = "ppg_vlans";

function isLoggedIn() {
  return Boolean(localStorage.getItem(TOKEN_KEY));
}

function requireLogin() {
  if (!isLoggedIn()) {
    window.location.href = "index.html";
  }
}

function login(username, password) {
  if (username === "admin" && password === "admin") {
    localStorage.setItem(TOKEN_KEY, "fake-token-" + Date.now());
    return true;
  }
  return false;
}

function logout() {
  localStorage.removeItem(TOKEN_KEY);
  window.location.href = "index.html";
}

function getVlans() {
  const raw = localStorage.getItem(VLAN_KEY);
  return raw ? JSON.parse(raw) : [];
}

function addVlan(vlan) {
  const list = getVlans();
  list.push({ ...vlan, createdAt: new Date().toISOString() });
  localStorage.setItem(VLAN_KEY, JSON.stringify(list));
}

// Tiny artificial delay so Playwright's networkidle wait has something to wait for.
function fakeDelay(ms = 400) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}
