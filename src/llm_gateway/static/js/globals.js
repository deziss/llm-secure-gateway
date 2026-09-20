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

// Robust error message formatter (handles strings, objects, and FastAPI 422 arrays)
window.formatErrorMessage = function (err, fallback = 'Operation failed') {
  if (!err) return fallback;
  if (typeof err === 'string') return err;
  if (Array.isArray(err.detail)) {
    return err.detail.map(e => {
      const field = Array.isArray(e.loc) && e.loc.length > 0 ? e.loc[e.loc.length - 1] : '';
      const msg = e.msg || 'Invalid field';
      return field && field !== 'body' ? field + ': ' + msg : msg;
    }).join(', ');
  }
  if (typeof err.detail === 'string') return err.detail;
  if (err.message) return err.message;
  return fallback;
};

// Session expiry handling
// A JWT cookie lives for one hour.  When it lapses, every XHR on an already
// open page starts coming back 401.  Send the user to the login screen once,
// rather than letting each caller surface its own unhelpful error.
let _redirectingToLogin = false;
window.handleSessionExpiry = function () {
  if (_redirectingToLogin) return;
  _redirectingToLogin = true;
  window.location.href = '/auth/login';
};

// DataTables ships with errMode 'alert', which turns any failed ajax call into
// a browser alert() reading only "Ajax error".  Replace it with handling that
// distinguishes an expired session from a genuine server fault, and reports
// the fault inside the table instead of in a modal dialog.
if (window.jQuery && $.fn.dataTable) {
  $.fn.dataTable.ext.errMode = 'none';

  $(document).on('error.dt', function (e, settings, techNote, message) {
    const xhr = settings && settings.jqXHR;
    const status = xhr ? xhr.status : 0;

    if (status === 401 || status === 403) {
      window.handleSessionExpiry();
      return;
    }

    const detail = status
      ? 'Server returned ' + status + ' ' + (xhr.statusText || '')
      : 'Could not reach the server';
    console.error('DataTables error:', message, detail);

    const tableNode = settings && settings.nTable;
    if (tableNode) {
      const colCount = $(tableNode).find('thead th').length || 1;
      $(tableNode).find('tbody').html(
        '<tr><td colspan="' + colCount + '" class="text-center py-8 text-red-500">' +
        '<div class="font-bold mb-1">Failed to load data</div>' +
        '<div class="text-xs text-slate-500">' + $('<div>').text(detail.trim()).html() + '</div>' +
        '</td></tr>'
      );
    }
  });
}

window.fetchWithCsrf = function (url, options = {}) {
  // Normalize URL to relative if targeting same host / localhost port
  let targetUrl = url;
  if (typeof targetUrl === 'string' && (targetUrl.startsWith('http://') || targetUrl.startsWith('https://'))) {
    try {
      const parsed = new URL(targetUrl, window.location.origin);
      if (
        parsed.host === window.location.host ||
        (parsed.port === window.location.port && (parsed.hostname === 'localhost' || parsed.hostname === '127.0.0.1'))
      ) {
        targetUrl = parsed.pathname + parsed.search + parsed.hash;
      }
    } catch (e) {
      // ignore URL parse errors
    }
  }

  options.headers = { ...options.headers, 'X-CSRF-Token': getCsrfToken() };
  if (!options.credentials) {
    options.credentials = 'include';
  }
  return fetch(targetUrl, options).then(function (response) {
    if (response.status === 401) {
      window.handleSessionExpiry();
    }
    return response;
  });
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
    await fetchWithCsrf('/auth/cookie/logout', { method: 'POST', credentials: 'include' });
    window.location.href = '/auth/login';
  } catch (e) {
    console.error('Logout failed:', e);
    window.location.href = '/auth/login';
  }
};
