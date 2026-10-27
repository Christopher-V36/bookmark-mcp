// bookmark-mcp web app. Plain ES module, no framework.
// Post content comes from strangers on the internet: it is only ever inserted as text.

const INTENTS = ["try", "build", "learn", "reference", "inspiration", "news", "noise"];
const INTENT_NAMES = {
  try: "Try",
  build: "Build",
  learn: "Learn",
  reference: "Reference",
  inspiration: "Inspiration",
  news: "News",
  noise: "Noise",
};
const USEFULNESS = [
  [0, "Skip"],
  [1, "Low"],
  [2, "Useful"],
  [3, "Must do"],
];
const EFFORTS = [
  ["quick", "Under 1 h"],
  ["session", "1–4 h"],
  ["project", "Days"],
];
const STATUSES = [
  ["todo", "To do"],
  ["doing", "Doing"],
  ["done", "Done"],
  ["skipped", "Skipped"],
];
const FILTER_KEYS = ["q", "collection", "intent", "status", "topic", "min_usefulness", "effort", "has_images", "sort", "hide_noise"];
const PAGE = 40;

const state = {
  view: "next",
  filters: {},
  overview: null,
  items: [],
  total: 0,
  selected: null,
  showAllTopics: false,
};

// --- tiny helpers ----------------------------------------------------------------

function h(tag, attrs = {}, ...children) {
  const el = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (value === undefined || value === null || value === false) continue;
    if (key === "class") el.className = value;
    else if (key.startsWith("on")) el.addEventListener(key.slice(2), value);
    else if (key === "dataset") Object.assign(el.dataset, value);
    else el.setAttribute(key, value === true ? "" : String(value));
  }
  for (const child of children.flat(Infinity)) {
    if (child === undefined || child === null || child === false) continue;
    el.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return el;
}

const $ = (selector) => document.querySelector(selector);

async function api(path, options = {}) {
  const init = { headers: {} , ...options };
  if (options.body !== undefined) {
    init.headers = { "Content-Type": "application/json", "X-Bookmark-Mcp": "1" };
    init.body = JSON.stringify(options.body);
  } else if (options.method && options.method !== "GET") {
    init.headers = { "Content-Type": "application/json", "X-Bookmark-Mcp": "1" };
    init.body = "{}";
  }
  const response = await fetch(path, init);
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.error || `Request failed (${response.status})`);
  return data;
}

let toastTimer;
function toast(message, isError = false) {
  const el = $("#toast");
  el.textContent = message;
  el.className = `toast show${isError ? " error" : ""}`;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => (el.className = "toast"), isError ? 4200 : 1600);
}

const dateFormat = new Intl.DateTimeFormat(undefined, { day: "numeric", month: "short", year: "numeric" });
function formatDate(iso) {
  if (!iso) return "";
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? "" : dateFormat.format(date);
}

function safeHref(url) {
  try {
    const parsed = new URL(url);
    return parsed.protocol === "https:" || parsed.protocol === "http:" ? parsed.href : null;
  } catch {
    return null;
  }
}

function domain(url) {
  try {
    return new URL(url).hostname.replace(/^www\./, "");
  } catch {
    return url;
  }
}

function firstLine(text, max = 140) {
  const line = (text || "").split("\n").find((l) => l.trim()) || "";
  return line.length > max ? `${line.slice(0, max - 1)}…` : line;
}

// --- routing -----------------------------------------------------------------------

function readHash() {
  const [path, query = ""] = location.hash.replace(/^#\/?/, "").split("?");
  state.view = ["next", "board", "posts"].includes(path) ? path : "next";
  const params = new URLSearchParams(query);
  state.filters = {};
  for (const key of FILTER_KEYS) if (params.get(key)) state.filters[key] = params.get(key);
}

function go(view, filters = state.filters) {
  const params = new URLSearchParams();
  for (const key of FILTER_KEYS) if (filters[key] !== undefined && filters[key] !== "") params.set(key, filters[key]);
  const query = params.toString();
  location.hash = `#/${view}${query ? `?${query}` : ""}`;
}

function setFilter(key, value) {
  const filters = { ...state.filters };
  if (value === undefined || value === "" || filters[key] === value) delete filters[key];
  else filters[key] = value;
  go(state.view === "next" || state.view === "board" ? "posts" : state.view, filters);
}

// --- rail ----------------------------------------------------------------------------

function facet(label, count, key, value, dotClass) {
  const pressed = state.view === "posts" && state.filters[key] === value;
  return h(
    "li",
    {},
    h(
      "button",
      { class: "facet", "aria-pressed": String(pressed), onclick: () => setFilter(key, value) },
      dotClass ? h("span", { class: `dot ${dotClass}` }) : null,
      h("span", {}, label),
      h("span", { class: "count" }, count),
    ),
  );
}

function renderRail() {
  for (const link of document.querySelectorAll(".views a")) {
    link.toggleAttribute("aria-current", link.dataset.view === state.view);
    if (link.dataset.view === state.view) link.setAttribute("aria-current", "page");
  }
  const o = state.overview;
  if (!o) return;
  const groups = [];

  groups.push(
    h("section", { class: "group" }, h("h2", {}, "Saved in"),
      h("ul", {}, o.collections.map((c) => facet(c.name === "bookmarks" ? "Bookmarks" : c.name === "likes" ? "Likes" : c.name, c.count, "collection", c.name)))),
  );
  groups.push(
    h("section", { class: "group" }, h("h2", {}, "Kind"),
      h("ul", {}, INTENTS.filter((i) => o.intents[i]).map((i) => facet(INTENT_NAMES[i], o.intents[i], "intent", i, `i-${i}`)))),
  );
  groups.push(
    h("section", { class: "group" }, h("h2", {}, "Progress"),
      h("ul", {}, STATUSES.filter(([s]) => o.statuses[s]).map(([s, name]) => facet(name, o.statuses[s], "status", s)))),
  );
  const topics = state.showAllTopics ? o.topics : o.topics.slice(0, 12);
  groups.push(
    h("section", { class: "group" }, h("h2", {}, "Topics"),
      h("ul", {}, topics.map((t) => facet(t.topic, t.count, "topic", t.topic))),
      o.topics.length > 12
        ? h("button", { class: "topics-more", onclick: () => { state.showAllTopics = !state.showAllTopics; renderRail(); } },
            state.showAllTopics ? "Show fewer" : `Show all ${o.topics.length}`)
        : null),
  );
  $("#filters").replaceChildren(...groups);
  $("#sync").textContent = o.last_capture ? `Last capture ${formatDate(o.last_capture)}` : "Nothing captured yet";
}

// --- rows ----------------------------------------------------------------------------

function intentLabel(intent) {
  return h("span", { class: `intent i-${intent || "unlabelled"}` }, intent ? INTENT_NAMES[intent] : "Not labelled");
}

function postMeta(item) {
  return h(
    "div",
    { class: "meta" },
    intentLabel(item.intent),
    item.status && item.status !== "todo"
      ? h("span", { class: `status-pill s-${item.status}` }, STATUSES.find(([s]) => s === item.status)?.[1])
      : null,
    item.effort ? h("span", {}, EFFORTS.find(([e]) => e === item.effort)?.[1]) : null,
    h("span", {}, item.author ? `@${item.author}` : null, item.created_at ? `, ${formatDate(item.created_at)}` : null),
    (item.topics || []).slice(0, 4).map((t) => h("span", { class: "topic" }, t)),
  );
}

function headlineFor(item) {
  return item.summary || item.article_title || firstLine(item.text) || "(no text)";
}

function postRow(item) {
  const classes = ["row", `u${item.usefulness ?? "x"}`];
  if (item.intent === "noise") classes.push("noise");
  if (state.selected === item.id) classes.push("selected");
  const thumb = item.thumb
    ? h("div", { class: "thumb-wrap" },
        h("img", { class: "thumb", src: item.thumb, alt: "", loading: "lazy" }),
        item.thumb_kind === "video" || item.thumb_kind === "gif" ? h("span", { class: "badge" }, item.thumb_kind === "gif" ? "GIF" : "Video") : null,
        item.media_count > 1 ? h("span", { class: "badge many" }, `${item.media_count}`) : null)
    : null;
  return h(
    "li",
    {
      class: classes.join(" "),
      tabindex: "0",
      dataset: { id: item.id },
      onclick: () => openPost(item.id),
      onkeydown: (event) => { if (event.key === "Enter") openPost(item.id); },
    },
    h("div", {},
      h("p", { class: "headline" }, h("span", { class: "mark" }, headlineFor(item))),
      item.action ? h("p", { class: "action" }, item.action) : item.summary ? null : h("p", { class: "raw-text" }, item.text),
      postMeta(item)),
    thumb,
  );
}

// --- views ---------------------------------------------------------------------------

async function renderNext() {
  const o = state.overview;
  const [doing, tries, builds, learns] = await Promise.all([
    api("/api/posts?status=doing&sort=useful&limit=10"),
    api("/api/next-up?intent=try&limit=6"),
    api("/api/next-up?intent=build&limit=6"),
    api("/api/next-up?intent=learn&limit=6"),
  ]);
  const toItem = (n) => ({ id: n.post_id, url: n.url, author: n.author_handle, created_at: n.created_at, intent: n.intent, usefulness: n.usefulness, effort: n.effort, summary: n.summary, action: n.action, status: n.status, topics: n.topics });
  const must = o.usefulness["3"] || 0;
  const useful = o.usefulness["2"] || 0;
  const section = (title, hint, items, link) =>
    h("section", {},
      h("h2", { class: "section-title" }, title, link ? h("a", { href: link }, "See all") : null),
      h("p", { class: "section-hint" }, hint),
      items.length ? h("ul", { class: "rows" }, items.map(postRow)) : h("p", { class: "empty" }, "Nothing here yet."));

  const parts = [
    h("h1", { class: "page-title" }, "What to do next"),
    h("p", { class: "lede" },
      o.remaining
        ? `${o.labeled} of ${o.saved_posts} saved posts are labelled. Ask Claude to run /triage-bookmarks to label the other ${o.remaining}.`
        : `All ${o.saved_posts} saved posts are labelled. The highlighted ones are worth acting on soon.`),
    h("div", { class: "tally" },
      h("div", { class: "must" }, h("b", {}, must), h("span", {}, "must do")),
      h("div", {}, h("b", {}, useful), h("span", {}, "useful")),
      h("div", {}, h("b", {}, o.statuses.doing || 0), h("span", {}, "in progress")),
      h("div", {}, h("b", {}, o.statuses.done || 0), h("span", {}, "done"))),
  ];
  if (doing.items.length) parts.push(section("In progress", "Things you've started.", doing.items, "#/posts?status=doing"));
  parts.push(
    section("Try", "Tools, models and apps you can use right away.", tries.items.map(toItem), "#/posts?intent=try&sort=useful"),
    section("Build", "Project ideas and things worth recreating.", builds.items.map(toItem), "#/posts?intent=build&sort=useful"),
    section("Learn", "Guides, courses and explainers.", learns.items.map(toItem), "#/posts?intent=learn&sort=useful"),
  );
  $("#view").replaceChildren(...parts);
}

function toolbar() {
  const f = state.filters;
  const input = h("input", {
    id: "search",
    type: "search",
    placeholder: "Search posts, summaries, notes and linked articles",
    value: f.q || "",
    "aria-label": "Search",
  });
  let timer;
  input.addEventListener("input", () => {
    clearTimeout(timer);
    timer = setTimeout(() => go("posts", { ...state.filters, q: input.value.trim() || undefined }), 280);
  });
  const sort = h("select", { "aria-label": "Sort", onchange: (e) => go("posts", { ...state.filters, sort: e.target.value }) },
    [["", f.q ? "Most useful" : "Recently saved"], ["useful", "Most useful"], ["saved", "Recently saved"], ["newest", "Newest posts"], ["oldest", "Oldest posts"]]
      .map(([value, label]) => h("option", { value, selected: (f.sort || "") === value }, label)));
  const noise = h("label", { class: "toggle" },
    h("input", { type: "checkbox", checked: f.hide_noise !== "false", onchange: (e) => go("posts", { ...state.filters, hide_noise: e.target.checked ? undefined : "false" }) }),
    "Hide noise");
  const images = h("label", { class: "toggle" },
    h("input", { type: "checkbox", checked: f.has_images === "true", onchange: (e) => go("posts", { ...state.filters, has_images: e.target.checked ? "true" : undefined }) }),
    "With images");
  const useful = h("select", { "aria-label": "Minimum usefulness", onchange: (e) => go("posts", { ...state.filters, min_usefulness: e.target.value || undefined }) },
    [["", "Any usefulness"], ["1", "Low and up"], ["2", "Useful and up"], ["3", "Must do only"]]
      .map(([value, label]) => h("option", { value, selected: (f.min_usefulness || "") === value }, label)));

  const active = [];
  const names = { collection: "In", intent: "Kind", status: "Progress", topic: "Topic", effort: "Effort" };
  for (const [key, label] of Object.entries(names)) {
    if (f[key]) {
      const value = key === "intent" ? INTENT_NAMES[f[key]] : key === "status" ? STATUSES.find(([s]) => s === f[key])?.[1] : f[key];
      active.push(h("button", { class: "chip-x", onclick: () => go("posts", { ...state.filters, [key]: undefined }), "aria-label": `Remove filter ${label} ${value}` },
        `${label}: ${value}`, h("span", { "aria-hidden": "true" }, "×")));
    }
  }
  return h("div", { class: "toolbar" },
    h("label", { class: "search" }, input, h("kbd", {}, "/")),
    sort, useful, noise, images,
    active.length ? h("div", { class: "active-filters" }, active) : null);
}

async function renderPosts(append = false) {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(state.filters)) params.set(key, value);
  params.set("limit", String(PAGE));
  params.set("offset", String(append ? state.items.length : 0));
  const data = await api(`/api/posts?${params}`);
  state.items = append ? [...state.items, ...data.items] : data.items;
  state.total = data.total;

  const list = state.items.length
    ? h("ul", { class: "rows", id: "rows" }, state.items.map(postRow))
    : h("p", { class: "empty" },
        state.filters.q
          ? `No saved post matches "${state.filters.q}". Try fewer words, or turn off "Hide noise".`
          : "No posts match these filters. Remove a filter to see more.");
  const more = state.items.length < state.total
    ? h("div", { class: "more" },
        h("button", { class: "button ghost", onclick: () => renderPosts(true) }, "Load more"),
        h("span", {}, `${state.items.length} of ${state.total}`))
    : state.total ? h("div", { class: "more" }, h("span", {}, `${state.total} posts`)) : null;
  $("#view").replaceChildren(
    h("h1", { class: "page-title" }, state.filters.q ? `Results for "${state.filters.q}"` : "All saved posts"),
    toolbar(),
    list,
    more,
  );
  if (append) document.querySelectorAll("#rows .row")[state.items.length - data.items.length]?.focus();
}

async function renderBoard() {
  const [todo, doing, done] = await Promise.all([
    api("/api/posts?status=todo&min_usefulness=2&sort=useful&limit=60"),
    api("/api/posts?status=doing&sort=useful&limit=60"),
    api("/api/posts?status=done&sort=saved&limit=60"),
  ]);
  const moves = {
    todo: [["doing", "Start"], ["skipped", "Skip"]],
    doing: [["done", "Done"], ["todo", "Back to to do"]],
    done: [["doing", "Reopen"]],
  };
  const column = (status, title, data) => {
    const col = h("section", {
      class: "column",
      dataset: { status },
      ondragover: (event) => { event.preventDefault(); col.classList.add("drop"); },
      ondragleave: () => col.classList.remove("drop"),
      ondrop: async (event) => {
        event.preventDefault();
        col.classList.remove("drop");
        const id = event.dataTransfer.getData("text/plain");
        if (id) await changeStatus(id, status);
      },
    },
    h("h2", {}, title, h("span", {}, data.total > data.items.length ? `${data.items.length} of ${data.total}` : data.total)),
    data.items.map((item) =>
      h("article", {
        class: `card u${item.usefulness ?? "x"}`,
        draggable: "true",
        ondragstart: (event) => event.dataTransfer.setData("text/plain", item.id),
      },
      h("p", { class: "headline", onclick: () => openPost(item.id) }, h("span", { class: "mark" }, headlineFor(item))),
      postMeta({ ...item, topics: (item.topics || []).slice(0, 2) }),
      h("div", { class: "card-actions" }, moves[status].map(([next, label]) =>
        h("button", { onclick: () => changeStatus(item.id, next) }, label))))));
    return col;
  };
  $("#view").replaceChildren(
    h("h1", { class: "page-title" }, "Board"),
    h("p", { class: "lede" }, "Useful and must-do posts you haven't finished. Drag a card, or use its buttons, to move it."),
    h("div", { class: "board" },
      column("todo", "To do", todo),
      column("doing", "Doing", doing),
      column("done", "Done", done)),
  );
}

async function changeStatus(id, status) {
  try {
    await api(`/api/posts/${encodeURIComponent(id)}/status`, { method: "PUT", body: { status } });
    toast(`Moved to ${STATUSES.find(([s]) => s === status)[1]}`);
    await refresh();
  } catch (error) {
    toast(error.message, true);
  }
}

// --- detail drawer -------------------------------------------------------------------

function segmented(label, options, current, onPick, extraClass = "") {
  return h("fieldset", { class: "field" },
    h("legend", {}, label),
    h("div", { class: `segmented ${extraClass}` }, options.map(([value, name]) =>
      h("button", {
        type: "button",
        dataset: { value: String(value) },
        "aria-pressed": String(String(current) === String(value)),
        onclick: () => onPick(value),
      }, name))));
}

function gallery(media) {
  const items = media.filter((m) => m.image || m.video_url);
  if (!items.length) return null;
  return h("div", { class: `gallery${items.length === 1 ? " single" : ""}` }, items.map((m) => {
    const figure = h("figure", { class: "shot" });
    if (m.image) {
      figure.append(h("a", { href: m.original || m.image, target: "_blank", rel: "noopener" },
        h("img", { src: m.image, alt: m.alt_text || "", loading: "lazy" })));
    }
    const video = m.video_url ? safeHref(m.video_url) : null;
    if (video) {
      figure.append(h("button", {
        class: "play",
        onclick: (event) => {
          event.stopPropagation();
          figure.replaceChildren(h("video", { src: video, controls: true, autoplay: true, poster: m.image || undefined, playsinline: true }));
        },
      }, m.kind === "gif" ? "Play GIF" : "Play video"));
    }
    if (m.alt_text) figure.append(h("figcaption", {}, m.alt_text));
    if (!m.image && m.status !== "done") figure.append(h("figcaption", {}, "Image still downloading."));
    return figure;
  }));
}

async function saveLabels(id, changes) {
  try {
    await api(`/api/posts/${encodeURIComponent(id)}/labels`, { method: "PATCH", body: changes });
    toast("Saved");
    await refresh({ keepDrawer: true });
  } catch (error) {
    toast(error.message, true);
  }
}

function tagEditor(values, onAdd, onRemove, placeholder) {
  const input = h("input", { class: "tag-add", placeholder, "aria-label": placeholder });
  input.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && input.value.trim()) {
      event.preventDefault();
      onAdd(input.value.trim());
    }
  });
  return h("div", { class: "tag-list" },
    values.map((value) => h("span", { class: "tag" }, value,
      h("button", { type: "button", "aria-label": `Remove ${value}`, onclick: () => onRemove(value) }, "×"))),
    input);
}

function renderDrawer(post) {
  const labels = post.labels || {};
  const id = post.id;
  const summary = h("input", { class: "text-input", value: labels.summary || "", placeholder: "One line: what is it?" });
  summary.addEventListener("change", () => saveLabels(id, { summary: summary.value }));
  const action = h("textarea", { placeholder: "One line: what will you do with it?" }, labels.action || "");
  action.addEventListener("change", () => saveLabels(id, { action: action.value }));
  const note = h("textarea", { placeholder: "Your notes (Claude can search these too)" }, post.note || "");
  note.addEventListener("change", async () => {
    try {
      await api(`/api/posts/${encodeURIComponent(id)}/note`, { method: "PUT", body: { note: note.value } });
      toast("Note saved");
    } catch (error) {
      toast(error.message, true);
    }
  });

  const statusField = labels.intent
    ? segmented("Progress", STATUSES, labels.status, (value) => changeStatus(id, value))
    : h("p", { class: "section-hint" }, "Choose a kind and usefulness to start tracking progress.");

  const quoted = post.quoted
    ? h("div", { class: "quoted" },
        h("div", { class: "byline" }, h("b", {}, post.quoted.author_name || ""), h("span", {}, post.quoted.author ? `@${post.quoted.author}` : "")),
        h("p", { class: "post-text" }, post.quoted.text),
        gallery(post.quoted.media))
    : null;
  const links = post.links.filter((l) => safeHref(l.url));
  const drawer = $("#drawer");
  const href = safeHref(post.url);
  drawer.replaceChildren(
    h("div", { class: "drawer-top" },
      href ? h("a", { href, target: "_blank", rel: "noopener noreferrer" }, "Open on X") : h("span"),
      h("button", { class: "close", onclick: closePost, "aria-label": "Close details" }, "Close")),
    segmented("Kind", INTENTS.map((i) => [i, INTENT_NAMES[i]]), labels.intent, (value) =>
      saveLabels(id, { intent: value, usefulness: labels.usefulness ?? (value === "noise" ? 0 : 2) })),
    segmented("Usefulness", USEFULNESS, labels.usefulness, (value) =>
      saveLabels(id, { usefulness: value, intent: labels.intent ?? "learn" }), "useful"),
    segmented("Effort", [...EFFORTS, ["", "Not set"]], labels.effort ?? "", (value) => saveLabels(id, { effort: value || null })),
    statusField,
    h("label", { class: "field" }, h("span", {}, "Summary"), summary),
    h("label", { class: "field" }, h("span", {}, "Next action"), action),
    h("div", { class: "field" }, h("span", {}, "Topics"),
      tagEditor(labels.topics || [],
        (value) => saveLabels(id, { topics: [...(labels.topics || []), value] }),
        (value) => saveLabels(id, { topics: (labels.topics || []).filter((t) => t !== value) }),
        "Add topic")),
    h("article", { class: "post" },
      h("div", { class: "byline" },
        h("b", {}, post.author_name || ""),
        h("span", {}, post.author ? `@${post.author}` : ""),
        h("span", {}, formatDate(post.created_at))),
      h("p", { class: "post-text" }, post.text),
      post.article
        ? h("details", { class: "article" }, h("summary", {}, post.article.title || "Article"), h("div", {}, post.article.text || ""))
        : null,
      gallery(post.media),
      quoted,
      links.length
        ? h("ul", { class: "links" }, links.map((l) => h("li", {},
            h("a", { href: safeHref(l.url), target: "_blank", rel: "noopener noreferrer" }, l.title || domain(l.url)),
            h("p", {}, l.description || domain(l.url)))))
        : null,
      h("p", { class: "metrics" },
        [["likes", "likes"], ["reposts", "reposts"], ["bookmarks", "bookmarks"], ["views", "views"]]
          .filter(([k]) => post.metrics?.[k] !== undefined)
          .map(([k, name]) => `${Number(post.metrics[k]).toLocaleString()} ${name}`)
          .join(", ")),
      h("p", { class: "metrics" }, `Saved in: ${post.collections.join(", ")}`)),
    h("label", { class: "field" }, h("span", {}, "Notes"), note),
    h("div", { class: "field" }, h("span", {}, "Tags"),
      tagEditor(post.tags,
        async (value) => {
          try {
            await api(`/api/posts/${encodeURIComponent(id)}/tags`, { method: "POST", body: { tag: value } });
            await openPost(id, { quiet: true });
          } catch (error) { toast(error.message, true); }
        },
        async (value) => {
          try {
            await api(`/api/posts/${encodeURIComponent(id)}/tags/${encodeURIComponent(value)}`, { method: "DELETE" });
            await openPost(id, { quiet: true });
          } catch (error) { toast(error.message, true); }
        },
        "Add tag")),
  );
}

async function openPost(id, { quiet = false } = {}) {
  try {
    const post = await api(`/api/posts/${encodeURIComponent(id)}`);
    state.selected = id;
    renderDrawer(post);
    $("#drawer").hidden = false;
    document.querySelector(".shell").classList.add("with-drawer");
    for (const row of document.querySelectorAll(".row")) row.classList.toggle("selected", row.dataset.id === id);
    if (!quiet) $("#drawer").scrollTop = 0;
  } catch (error) {
    toast(error.message, true);
  }
}

function closePost() {
  state.selected = null;
  $("#drawer").hidden = true;
  document.querySelector(".shell").classList.remove("with-drawer");
  for (const row of document.querySelectorAll(".row.selected")) row.classList.remove("selected");
}

// --- main loop -----------------------------------------------------------------------

async function refresh({ keepDrawer = false } = {}) {
  try {
    state.overview = await api("/api/overview");
    renderRail();
    if (state.view === "next") await renderNext();
    else if (state.view === "board") await renderBoard();
    else await renderPosts();
    if (keepDrawer && state.selected) await openPost(state.selected, { quiet: true });
  } catch (error) {
    $("#view").replaceChildren(
      h("h1", { class: "page-title" }, "Can't reach your bookmarks"),
      h("p", { class: "lede" }, `${error.message}. Check that "bookmark-mcp web" is still running, then reload this page.`),
    );
  }
}

window.addEventListener("hashchange", () => {
  readHash();
  refresh({ keepDrawer: true });
});

document.addEventListener("keydown", (event) => {
  const typing = /^(INPUT|TEXTAREA|SELECT)$/.test(event.target.tagName);
  if (event.key === "Escape") {
    if (typing) event.target.blur();
    else closePost();
    return;
  }
  if (typing) return;
  if (event.key === "/") {
    event.preventDefault();
    if (state.view !== "posts") go("posts");
    setTimeout(() => $("#search")?.focus(), 60);
  } else if (event.key === "j" || event.key === "k") {
    const rows = [...document.querySelectorAll(".row")];
    if (!rows.length) return;
    const index = rows.indexOf(document.activeElement);
    const next = rows[Math.max(0, Math.min(rows.length - 1, index + (event.key === "j" ? 1 : -1)))];
    next.focus();
    next.scrollIntoView({ block: "nearest" });
  }
});

readHash();
refresh();
