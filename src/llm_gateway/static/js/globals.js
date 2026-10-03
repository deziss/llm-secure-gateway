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

  // autoWidth makes DataTables measure the table once and write fixed pixel
  // widths onto it and its columns. Those don't adapt to the sidebar or the
  // window, so wide tables overflowed their card. Let the browser size them.
  $.extend(true, $.fn.dataTable.defaults, {
    autoWidth: false,
    // The last column is always row actions: sorting it is meaningless and its
    // sort arrows were clutter.
    columnDefs: [{ targets: -1, orderable: false }],
  });

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

// Surface unexpected script errors instead of failing silently. Several
// "button does nothing" reports had no visible symptom at all; this turns
// them into a message the user can act on (and quote in a bug report).
(function () {
  var last = 0;
  function report(msg) {
    var now = Date.now();
    if (now - last < 4000) return; // don't stack toasts for cascading errors
    last = now;
    if (typeof showToast === "function") {
      showToast("Something went wrong", String(msg || "Unexpected error") +
        ". Try reloading the page (Ctrl+Shift+R).", "error");
    }
  }
  window.addEventListener("error", function (e) {
    // Ignore failed resource loads (img/script 404s); only script errors.
    if (e && e.message) report(e.message);
  });
  window.addEventListener("unhandledrejection", function (e) {
    var r = e && e.reason;
    // fetch() network failures are already reported by their callers.
    if (r && r.name === "AbortError") return;
    report(r && r.message ? r.message : r);
  });
})();
