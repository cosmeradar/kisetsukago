/* ok: アフィリエイトリンクのクリックを GA4 のイベントとして送る。第一者のスクリプトで、Cookie は増やさない。リンクの HTML は書き換えない */
(function () {
  "use strict";

  if (typeof window.gtag !== "function") return;

  // dest: 飛び先がリンクの中に書かれている引数。あれば、その飛び先を link_url に入れる
  // （元の URL は長く、100字で切ると飛び先が残らないため）
  var HOSTS = [
    { re: /(^|\.)a8\.net$/, network: "a8", dest: "a8ejpredirect" },
    { re: /(^|\.)moshimo\.com$/, network: "moshimo", dest: "url" },
    { re: /(^|\.)afi-b\.com$/, network: "afi-b" },
    { re: /(^|\.)felmat\.net$/, network: "felmat" },
    { re: /(^|\.)accesstrade\.net$/, network: "accesstrade" },
    { re: /(^|\.)valuecommerce\.(com|ne\.jp)$/, network: "valuecommerce", dest: "vc_url" },
    { re: /^hb\.afl\.rakuten\.co\.jp$/, network: "rakuten", dest: "pc" },
    { re: /^amzn\.to$/, network: "amazon" },
    { re: /(^|\.)amazon\.co\.jp$/, network: "amazon", needTag: true }
  ];

  function match(href) {
    var u;
    try {
      u = new URL(href, location.href);
    } catch (e) {
      return null;
    }
    for (var i = 0; i < HOSTS.length; i++) {
      var h = HOSTS[i];
      if (!h.re.test(u.hostname)) continue;
      if (h.needTag && !u.searchParams.get("tag")) continue;
      var dest = h.dest ? u.searchParams.get(h.dest) : null;
      if (dest && !/^https?:\/\//i.test(dest)) dest = null;
      return { network: h.network, host: u.hostname, url: dest || u.href };
    }
    return null;
  }

  document.addEventListener(
    "click",
    function (ev) {
      var el = ev.target;
      if (!el || typeof el.closest !== "function") return;
      var a = el.closest("a[href]");
      if (!a) return;

      var hit = match(a.getAttribute("href"));
      if (!hit) return;

      var a8mat = null;
      var m = a.href.match(/[?&]a8mat=([^&]+)/);
      if (m) {
        try {
          a8mat = decodeURIComponent(m[1]);
        } catch (e) {
          a8mat = m[1];
        }
      }

      window.gtag("event", "affiliate_click", {
        network: hit.network,
        a8mat: a8mat || "(none)",
        link_text: (a.textContent || "").replace(/\s+/g, " ").trim().slice(0, 80),
        link_domain: hit.host,
        link_url: hit.url.slice(0, 100)
      });
    },
    true
  );
})();
