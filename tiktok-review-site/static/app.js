(() => {
  "use strict";

  const $ = id => document.getElementById(id);
  let accounts = [];
  let creator = null;
  let currentPublishId = "";
  let pollTimer = null;

  function esc(value) {
    return String(value ?? "").replace(/[&<>"']/g, ch => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
    }[ch]));
  }

  async function api(url, options = {}) {
    const response = await fetch(url, options);
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.detail || ("HTTP " + response.status));
    return data;
  }

  async function loadConfig() {
    try {
      const cfg = await api("/api/config");
      const badge = $("apiBadge");
      if (cfg.tiktok_configured) {
        badge.textContent = "TikTok API configured";
        badge.className = "status-badge good";
      } else {
        badge.textContent = "TikTok API keys not configured";
        badge.className = "status-badge bad";
      }
    } catch {
      $("apiBadge").textContent = "Server unavailable";
      $("apiBadge").className = "status-badge bad";
    }
  }

  async function loadAccounts() {
    const root = $("accounts");
    root.innerHTML = '<div class="panel-copy">Loading authorized accounts…</div>';

    try {
      const payload = await api("/api/accounts");
      accounts = payload.accounts || [];
      renderAccounts();
      renderAccountSelect();
    } catch (error) {
      root.innerHTML = '<div class="publish-status bad">' + esc(error.message) + '</div>';
    }
  }

  function renderAccounts() {
    const root = $("accounts");
    const bySlot = new Map(accounts.map(a => [Number(a.slot), a]));
    let html = "";

    for (let slot = 1; slot <= 3; slot++) {
      const account = bySlot.get(slot);
      if (account) {
        const avatar = account.avatar_url
          ? '<img src="' + esc(account.avatar_url) + '" alt="">'
          : '<div class="avatar-placeholder">TT</div>';
        html +=
          '<div class="account-card">' +
            avatar +
            '<div><strong>' + esc(account.display_name || ("TikTok account " + slot)) + '</strong>' +
              '<span>Account slot ' + slot + ' · Authorized with TikTok</span></div>' +
            '<div class="account-actions">' +
              '<a class="button" href="/connect/tiktok/' + slot + '">Reconnect</a>' +
              '<button class="ghost-button" data-disconnect="' + slot + '">Disconnect</button>' +
            '</div>' +
          '</div>';
      } else {
        html +=
          '<div class="connect-card">' +
            '<span>TikTok account slot ' + slot + '</span>' +
            '<a class="button primary" href="/connect/tiktok/' + slot + '">Connect TikTok</a>' +
          '</div>';
      }
    }

    root.innerHTML = html;
    root.querySelectorAll("[data-disconnect]").forEach(button => {
      button.addEventListener("click", async () => {
        const slot = Number(button.dataset.disconnect);
        if (!confirm("Disconnect TikTok account slot " + slot + "?")) return;
        try {
          await api("/api/accounts/" + slot, { method: "DELETE" });
          await loadAccounts();
        } catch (error) {
          alert(error.message);
        }
      });
    });
  }

  function renderAccountSelect() {
    const select = $("accountSelect");
    if (!accounts.length) {
      select.innerHTML = '<option value="">Connect a TikTok account first</option>';
      resetCreator();
      return;
    }
    select.innerHTML = accounts.map(a =>
      '<option value="' + Number(a.slot) + '">' +
        esc(a.display_name || ("TikTok account " + a.slot)) +
        ' · slot ' + Number(a.slot) +
      '</option>'
    ).join("");
    loadCreatorInfo();
  }

  function privacyLabel(value) {
    return ({
      PUBLIC_TO_EVERYONE: "Everyone",
      MUTUAL_FOLLOW_FRIENDS: "Friends",
      FOLLOWER_OF_CREATOR: "Followers",
      SELF_ONLY: "Only me"
    })[value] || value;
  }

  function resetCreator() {
    creator = null;
    $("creatorCard").classList.add("hidden");
    $("privacy").innerHTML = '<option value="">Connect an account to load current TikTok options</option>';
    for (const id of ["comments", "duet", "stitch"]) {
      $(id).checked = false;
      $(id).disabled = true;
    }
  }

  async function loadCreatorInfo() {
    const slot = Number($("accountSelect").value);
    if (!slot) return resetCreator();

    resetCreator();
    $("privacy").innerHTML = '<option value="">Loading current TikTok settings…</option>';

    try {
      const payload = await api("/api/tiktok/creator-info/" + slot);
      creator = payload.creator || {};

      const card = $("creatorCard");
      const avatar = creator.creator_avatar_url
        ? '<img src="' + esc(creator.creator_avatar_url) + '" alt="">'
        : '<div class="avatar-placeholder">TT</div>';
      card.innerHTML =
        avatar +
        '<div><strong>' + esc(creator.creator_nickname || creator.creator_username || "TikTok creator") + '</strong>' +
          '<span>@' + esc(creator.creator_username || "authorized creator") +
          ' · max video ' + Number(creator.max_video_post_duration_sec || 0) + 's</span></div>';
      card.classList.remove("hidden");

      const options = creator.privacy_level_options || [];
      $("privacy").innerHTML = options.map(value =>
        '<option value="' + esc(value) + '">' + esc(privacyLabel(value)) + '</option>'
      ).join("");

      [
        ["comments", "comment_disabled"],
        ["duet", "duet_disabled"],
        ["stitch", "stitch_disabled"]
      ].forEach(([id, disabledField]) => {
        const input = $(id);
        const disabled = Boolean(creator[disabledField]);
        input.disabled = disabled;
        input.checked = !disabled;
        input.closest(".toggle")?.classList.toggle("disabled", disabled);
      });
    } catch (error) {
      $("privacy").innerHTML = '<option value="">Could not load TikTok settings</option>';
      showStatus(error.message, "bad");
    }
  }

  function showStatus(message, type = "") {
    const box = $("publishStatus");
    box.textContent = message;
    box.className = "publish-status" + (type ? " " + type : "");
  }

  async function publish(event) {
    event.preventDefault();
    const slot = Number($("accountSelect").value);
    const file = $("videoFile").files[0];
    if (!slot || !file) {
      showStatus("Choose a connected TikTok account and video file.", "bad");
      return;
    }
    if (!$("consent").checked) {
      showStatus("Explicit consent is required before the video can be sent.", "bad");
      return;
    }

    const form = new FormData();
    form.append("video", file);
    form.append("title", $("caption").value);
    form.append("privacy_level", $("privacy").value);
    form.append("allow_comment", $("comments").checked ? "true" : "false");
    form.append("allow_duet", $("duet").checked ? "true" : "false");
    form.append("allow_stitch", $("stitch").checked ? "true" : "false");
    form.append("paid_partnership", $("paid").checked ? "true" : "false");
    form.append("own_business", $("ownBusiness").checked ? "true" : "false");
    form.append("is_aigc", $("aigc").checked ? "true" : "false");
    form.append("consent", "true");

    const button = $("publishButton");
    button.disabled = true;
    button.textContent = "Sending to TikTok…";
    showStatus("Uploading the creator-selected video to TikTok. Please keep this page open.");

    try {
      const response = await fetch("/api/tiktok/publish/" + slot, {
        method: "POST",
        body: form
      });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(payload.detail || ("HTTP " + response.status));

      currentPublishId = payload.publish_id;
      showStatus(payload.message + " Publish ID: " + currentPublishId, "good");
      startPolling(slot, currentPublishId);
    } catch (error) {
      showStatus(error.message, "bad");
    } finally {
      button.disabled = false;
      button.textContent = "Publish to TikTok";
    }
  }

  function startPolling(slot, publishId) {
    if (pollTimer) clearInterval(pollTimer);

    const tick = async () => {
      try {
        const payload = await api("/api/tiktok/status/" + slot + "/" + encodeURIComponent(publishId));
        const status = payload.status || {};
        const value = status.status || "PROCESSING";
        const reason = status.fail_reason ? " · " + status.fail_reason : "";

        if (value === "PUBLISH_COMPLETE") {
          showStatus("TikTok status: PUBLISH_COMPLETE. The post has completed processing.", "good");
          clearInterval(pollTimer);
          pollTimer = null;
        } else if (value === "FAILED") {
          showStatus("TikTok status: FAILED" + reason, "bad");
          clearInterval(pollTimer);
          pollTimer = null;
        } else {
          showStatus("TikTok status: " + value + ". Processing can take a few minutes.");
        }
      } catch (error) {
        showStatus("Status check: " + error.message, "bad");
      }
    };

    tick();
    pollTimer = setInterval(tick, 4000);
  }

  $("refreshAccounts").addEventListener("click", loadAccounts);
  $("accountSelect").addEventListener("change", loadCreatorInfo);
  $("caption").addEventListener("input", () => {
    $("captionCount").textContent = $("caption").value.length;
  });
  $("videoFile").addEventListener("change", () => {
    const file = $("videoFile").files[0];
    $("videoFileName").textContent = file ? file.name : "No file selected";
  });
  $("publishForm").addEventListener("submit", publish);

  loadConfig();
  loadAccounts();
})();
