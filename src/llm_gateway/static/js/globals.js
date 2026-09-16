// globals.js — Global constants and shared functions
// BASE_URL and USER_ROLE are set via inline <script> in base.html as window.BASE_URL / window.USER_ROLE

const backendsData = {};
const ownersData = {};
const usersData = {};

// CSRF token helpers
window.getCsrfToken = function () {
  const match = document.cookie.split('; ').find(c => c.startsWith('csrf_token='));
  return match ? match.split('=')[1] : '';
};

window.fetchWithCsrf = function (url, options = {}) {
  options.headers = { ...options.headers, 'X-CSRF-Token': getCsrfToken() };
  return fetch(url, options);
};

// XSS sanitization helper
window.sanitizeHTML = function (html) {
  if (typeof DOMPurify !== 'undefined') {
    return DOMPurify.sanitize(html);
  }
  return html;
};

window.logout = async function () {
  try {
    await fetchWithCsrf(BASE_URL + '/auth/cookie/logout', { method: 'POST', credentials: 'include' });
    window.location.href = '/auth/login';
  } catch (e) {
    console.error('Logout failed:', e);
    window.location.href = '/auth/login';
  }
};
