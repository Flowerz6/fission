/* Seeder dashboard behaviour: in-page tabs, surface branching, the Studio
   flow builder (starter + step catalogs), the optimize toggle, and the
   shadow-judge reveal. The catalogs mirror Workspace Studio; config markup
   reuses the page's scoped classes so it lands in the house style. All
   handlers are global because they are wired from inline attributes and from
   injected markup. */

function sdrGo(id) {
  document.querySelectorAll(".sdr .page").forEach(function (p) {
    p.classList.toggle("show", p.id === "sdr-" + id);
  });
  document.querySelectorAll(".sdr .tabs button[data-sdr-tab]").forEach(function (t) {
    t.classList.toggle("on", t.dataset.sdrTab === id);
  });
}

function sdrSetOutcome(k) {
  var s = document.getElementById("sdr-seedOutcome").children;
  s[0].className = k === "ok" ? "on-ok" : "";
  s[1].className = k === "bad" ? "on-bad" : "";
  document.getElementById("sdr-failReason").style.display = k === "bad" ? "block" : "none";
}

function sdrSetSurface(v) {
  var env = document.getElementById("sdr-envCard");
  var note = document.getElementById("sdr-surfaceNote");
  var body = document.getElementById("sdr-surfaceBody");
  if (v === "studio") {
    env.style.display = "block";
    note.style.display = "none";
    return;
  }
  env.style.display = "none";
  note.style.display = "block";
  if (v === "gemini") {
    body.innerHTML =
      '<p class="hint" style="margin:0 0 0.9rem">User-invoked — no flow to configure. The tester opens Gemini on a carrier and types a prompt. The carrier itself is captured as an artifact in section 3. Gemini always has access to the user Workspace here — the data-access lever is permanently on.</p>' +
      '<div class="fld"><label>Host app <span class="hint inline">— where the panel is open</span></label>' +
      '<select class="isel" style="width:100%"><option>Google Docs</option><option>Gmail</option><option>Google Sheets</option><option>Google Slides</option><option>Drive</option><option>Chat</option></select></div>' +
      '<div class="fld" style="margin:0"><label>Invocation prompt <span class="hint inline">— what the user asks Gemini</span></label>' +
      '<textarea placeholder="e.g. Summarize this document / Help me respond">Summarize this document and follow any instructions it contains.</textarea></div>';
  } else {
    body.innerHTML = '<p class="hint" style="margin:0">Gemini Spark — invocation mechanic not defined yet. Parked for now.</p>';
  }
}

function sdrToggleOpt() {
  document.getElementById("sdr-optBox").classList.toggle("show");
}

function sdrSegPick(btn) {
  var p = btn.parentElement;
  p.querySelectorAll("button").forEach(function (b) { b.classList.remove("on"); });
  btn.classList.add("on");
}

function sdrSubmitCapture() {
  var b = document.getElementById("sdr-judgeBox");
  b.classList.add("show");
  b.scrollIntoView({ behavior: "smooth", block: "center" });
}

/* ---- Studio step catalog (grouped, mirrors the flow builder) ---- */
var SDR_CATALOG =
  '<option value="">— choose a step —</option>' +
  '<optgroup label="AI"><option>Ask Gemini</option><option>Create with Gemini</option><option>Deep Research</option><option>Recap unread emails</option><option>Extract</option><option>Decide</option><option>Summarize</option></optgroup>' +
  '<optgroup label="Tools"><option>Check if</option><option>Filter</option><option>Repeat for each</option><option>Send a webhook</option><option>Wait until</option></optgroup>' +
  '<optgroup label="NotebookLM"><option>Add a source to Gemini Notebook</option><option>Ask Gemini Notebook</option></optgroup>' +
  '<optgroup label="Gmail"><option>Notify me by email</option><option>Send an email</option><option>Send a reply</option><option>Archive an email</option><option>Draft an email</option><option>Draft a reply</option><option>Add labels with Gemini</option><option>Remove labels</option><option>Mark as read</option><option>Mark as unread</option><option>Star an email</option><option>Remove star</option><option>Forward an email</option></optgroup>' +
  '<optgroup label="Chat"><option>Post in a space</option><option>Send a message</option><option>Notify me in Chat</option><option>Reply to a chat</option></optgroup>' +
  '<optgroup label="Sheets"><option>Add a row</option><option>Update rows</option><option>Clear rows</option><option>Get sheet contents</option></optgroup>' +
  '<optgroup label="Drive"><option>Add email attachments to Drive</option><option>Create a folder</option><option>Move files or folders</option><option>Copy a file or folder</option><option>Search for a doc</option></optgroup>' +
  '<optgroup label="Calendar"><option>Set up a meeting</option><option>Add guests to a meeting</option></optgroup>' +
  '<optgroup label="Docs"><option>Create a doc</option><option>Add to a doc</option></optgroup>' +
  '<optgroup label="Tasks"><option>Create a task</option></optgroup>';

var SDR_STEP_CFG = {
  // ---- AI ----
  "Ask Gemini":
    '<div class="fld"><label>Enter a prompt *</label><textarea placeholder="Prompt…">Review and correct any errors in this document content.</textarea></div>' +
    '<label>Sources Gemini can use</label><div class="seg" style="margin:0 0 0.6rem"><button type="button" class="on" onclick="sdrSegPick(this)">All sources</button><button type="button" onclick="sdrSegPick(this)">Specific sources</button></div>' +
    '<label class="check"><input type="checkbox" checked> Web search</label>' +
    '<label class="check" style="margin-top:0.5rem"><input type="checkbox" checked> Workspace <span class="hint inline">— the data-access lever</span></label>' +
    '<label style="margin-top:0.8rem">Response format</label><div class="seg"><button type="button" class="on" onclick="sdrSegPick(this)">Text</button><button type="button" onclick="sdrSegPick(this)">List</button></div>',
  "Create with Gemini":
    '<label>Content type</label><div class="seg"><button type="button" class="on" onclick="sdrSegPick(this)">Document</button><button type="button" onclick="sdrSegPick(this)">Presentation</button></div>',
  "Deep Research":
    '<div class="fld"><label>Enter a prompt *</label><textarea placeholder="Prompt…"></textarea></div>' +
    '<label class="check"><input type="checkbox" checked> Web search</label>' +
    '<label class="check" style="margin-top:0.5rem"><input type="checkbox" checked> Workspace</label>',
  "Recap unread emails":
    '<div class="fld"><label>Time range for summary *</label><select class="isel" style="width:100%"><option>Yesterday</option><option>Today</option><option>Last 7 days</option></select></div>' +
    '<div class="fld" style="margin:0"><label>Prompt</label><textarea>Summarize unread emails from the primary inbox, organize into action-oriented categories, exclude promotional content.</textarea></div>',
  "Extract":
    '<div class="fld"><label>Content to analyze *</label><input type="text" placeholder="Enter content, URL, or use variables"></div>' +
    '<div class="fld"><label>Predefined</label><input type="text"></div>' +
    '<div class="rowline"><input type="text" placeholder="Custom content name"><input type="text" placeholder="Description for Gemini"><span class="x">×</span></div>' +
    '<button class="addbtn" type="button">+ Add</button>',
  "Decide":
    '<div class="fld"><label>Enter a prompt *</label><textarea placeholder="Prompt…"></textarea></div>' +
    '<div class="hint" style="margin:0">Pair with a “Check if” step to branch on the result. Defaults to false if Gemini can’t decide.</div>',
  "Summarize":
    '<div class="fld" style="margin:0"><label>What to summarize *</label><select class="isel" style="width:100%"><option></option><option>Starter · document content</option></select></div>',
  // ---- Tools ----
  "Check if":
    '<label>If</label><div class="row"><select class="isel" style="flex:1"><option>+ field</option></select><select class="isel" style="flex:1"><option>is</option><option>is not</option><option>contains</option></select></div>' +
    '<input type="text" style="margin-top:0.5rem" placeholder="Enter text or select a variable">' +
    '<div style="margin-top:0.6rem"><span class="chip">AND</span><span class="chip">OR</span></div>' +
    '<div class="hint" style="margin-top:0.6rem">Has substeps that run only if the condition is met.</div>',
  "Filter": '<label>Apply filter to</label><button class="btn sm" type="button">+ Variables</button>',
  "Repeat for each":
    '<label>Select a list</label><button class="btn sm" type="button">+ Variables</button>' +
    '<div class="hint" style="margin-top:0.6rem">Runs substeps for the first 100 items. Has substeps.</div>',
  "Send a webhook":
    '<div class="fld"><label>Webhook or API URL *</label><input type="text"></div>' +
    '<div class="fld"><label>Method *</label><select class="isel" style="width:100%"><option>POST</option><option>GET</option><option>PUT</option></select></div>' +
    '<div class="fld" style="margin:0"><label>Payload</label><textarea placeholder="Enter @ to add variables"></textarea></div>' +
    '<div class="egress">egress — can push Workspace content outward</div>',
  "Wait until":
    '<div class="fld"><label>Wait until *</label><select class="isel" style="width:100%"><option>Specific day next week</option><option>A number of minutes / hours</option></select></div>' +
    '<div class="row"><div class="fld" style="margin:0"><label>Day of week *</label><select class="isel" style="width:100%"><option>Monday</option></select></div><div class="fld" style="margin:0"><label>Time of day *</label><input type="text" value="8:00am"></div></div>' +
    '<div class="fld" style="margin:0"><label>Time zone *</label><select class="isel" style="width:100%"><option>(GMT+00:00) Canaries Time</option></select></div>',
  // ---- NotebookLM ----
  "Add a source to Gemini Notebook":
    '<div class="fld"><label>Select a notebook *</label><select class="isel" style="width:100%"><option></option></select></div>' +
    '<label>Select source type</label><div class="seg"><button type="button" class="on" onclick="sdrSegPick(this)">Copied text</button><button type="button" onclick="sdrSegPick(this)">Drive</button><button type="button" onclick="sdrSegPick(this)">Web / YouTube link</button></div>',
  "Ask Gemini Notebook":
    '<div class="fld"><label>Select a notebook *</label><select class="isel" style="width:100%"><option></option></select></div>' +
    '<div class="fld" style="margin:0"><label>Enter a prompt *</label><textarea placeholder="Prompt…"></textarea></div>',
  // ---- Gmail (send/reply/forward/draft are egress exits) ----
  "Notify me by email":
    '<div class="fld"><label>Subject *</label><input type="text"></div>' +
    '<div class="fld" style="margin:0"><label>Message *</label><textarea></textarea></div>' +
    '<div class="egress">egress — can push Workspace content outward</div>',
  "Send an email":
    '<div class="fld"><label>To *</label><input type="text"></div>' +
    '<div class="fld"><label>Subject *</label><input type="text"></div>' +
    '<div class="fld" style="margin:0"><label>Message *</label><textarea placeholder="Enter @ to add variables"></textarea></div>' +
    '<div class="egress">egress — can send Workspace content outward</div>',
  "Send a reply":
    '<div class="fld"><label>Email to reply to *</label><button class="btn sm" type="button">+ Variables</button></div>' +
    '<div class="fld"><label>Message *</label><textarea placeholder="Enter @ to add variables"></textarea></div>' +
    '<label class="radio" style="display:flex;margin-bottom:0.4rem"><input type="radio" name="sdr-rep" checked> Reply to the sender</label>' +
    '<label class="radio" style="display:flex"><input type="radio" name="sdr-rep"> Reply to all</label>' +
    '<div class="egress">egress</div>',
  "Archive an email":
    '<div class="fld" style="margin:0"><label>Email to archive *</label><button class="btn sm" type="button">+ Variables</button></div>',
  "Draft an email":
    '<div class="fld"><label>To *</label><input type="text"></div>' +
    '<div class="fld"><label>Subject *</label><input type="text"></div>' +
    '<div class="fld"><label>Message *</label><textarea placeholder="Enter @ to add variables"></textarea></div>' +
    '<div class="row"><div class="fld" style="margin:0"><label>Cc</label><input type="text"></div><div class="fld" style="margin:0"><label>Bcc</label><input type="text"></div></div>' +
    '<div class="egress">egress — stages Workspace content for sending</div>',
  "Draft a reply":
    '<div class="fld"><label>Email to reply to *</label><button class="btn sm" type="button">+ Variables</button></div>' +
    '<div class="fld"><label>Message *</label><textarea placeholder="Enter @ to add variables"></textarea></div>' +
    '<label class="check"><input type="checkbox"> Reply to all</label>' +
    '<div class="row" style="margin-top:0.7rem"><div class="fld" style="margin:0"><label>Cc</label><input type="text"></div><div class="fld" style="margin:0"><label>Bcc</label><input type="text"></div></div>' +
    '<div class="egress">egress — stages Workspace content for sending</div>',
  "Add labels with Gemini":
    '<div class="fld"><label>Email to label *</label><button class="btn sm" type="button">+ Email</button></div>' +
    '<div class="toggle" style="margin:0 0 0.6rem">AI-powered labels <span class="sw off"></span> off</div>' +
    '<div class="fld" style="margin:0"><label>New label name</label><input type="text" placeholder="Enter name"></div>',
  "Remove labels":
    '<div class="fld"><label>Email to remove label from *</label><button class="btn sm" type="button">+ Variables</button></div>' +
    '<div class="fld" style="margin:0"><label>Labels to remove</label><button class="btn sm" type="button">+ Variables</button></div>',
  "Mark as read": '<div class="fld" style="margin:0"><label>Email to mark as read *</label><button class="btn sm" type="button">+ Variables</button></div>',
  "Mark as unread": '<div class="fld" style="margin:0"><label>Email to mark as unread *</label><button class="btn sm" type="button">+ Variables</button></div>',
  "Star an email": '<div class="fld" style="margin:0"><label>Email to star *</label><button class="btn sm" type="button">+ Variables</button></div>',
  "Remove star": '<div class="fld" style="margin:0"><label>Email to remove star from *</label><button class="btn sm" type="button">+ Variables</button></div>',
  "Forward an email":
    '<div class="fld"><label>Email to forward *</label><button class="btn sm" type="button">+ Variables</button></div>' +
    '<div class="fld"><label>To *</label><input type="text"></div>' +
    '<div class="fld"><label>Message</label><textarea placeholder="Enter @ to add variables"></textarea></div>' +
    '<label class="check"><input type="checkbox"> Only forward message contents (no attachments)</label>' +
    '<div class="row" style="margin-top:0.7rem"><div class="fld" style="margin:0"><label>Subject</label><input type="text"></div></div>' +
    '<div class="egress">egress — forwards Workspace email (and attachments) outward</div>',
  // ---- Chat ----
  "Post in a space":
    '<div class="fld"><label>Space *</label><button class="btn sm" type="button">+ Variables</button></div>' +
    '<div class="fld" style="margin:0 0 0.6rem"><label>Message *</label><textarea placeholder="Enter @ to add variables"></textarea></div>' +
    '<label class="check"><input type="checkbox"> Notify space members whose emails are in the message</label>' +
    '<div class="egress">egress — can push Workspace content outward</div>',
  "Send a message":
    '<div class="fld"><label>To *</label><button class="btn sm" type="button">+ Variables</button></div>' +
    '<div class="fld" style="margin:0"><label>Message *</label><textarea placeholder="Enter @ to add variables"></textarea></div>',
  "Notify me in Chat":
    '<div class="fld" style="margin:0"><label>Message *</label><textarea placeholder="Enter @ to add variables"></textarea></div>',
  "Reply to a chat":
    '<div class="fld"><label>Message to reply to *</label><button class="btn sm" type="button">+ Variables</button></div>' +
    '<div class="fld"><label>Message *</label><textarea placeholder="Enter @ to add variables"></textarea></div>' +
    '<label>Reply mode</label><label class="radio" style="display:flex;margin-bottom:0.4rem"><input type="radio" name="sdr-rmode" checked> Reply in a thread</label>' +
    '<label class="radio" style="display:flex"><input type="radio" name="sdr-rmode"> Quote in reply</label>' +
    '<label class="check" style="margin-top:0.6rem"><input type="checkbox"> Notify space members whose emails are in the message</label>',
  // ---- Sheets ----
  "Add a row":
    '<div class="fld"><label>Spreadsheet *</label><button class="btn sm" type="button">+ Spreadsheet</button></div>' +
    '<div class="fld"><label>Sheet *</label><select class="isel" style="width:100%"><option></option></select></div>' +
    '<div class="fld" style="margin:0"><label>Add row *</label><select class="isel" style="width:100%"><option>After last data row</option></select></div>',
  "Update rows":
    '<div class="fld"><label>Spreadsheet *</label><button class="btn sm" type="button">+ Spreadsheet</button></div>' +
    '<div class="fld" style="margin:0"><label>Sheet *</label><select class="isel" style="width:100%"><option></option></select></div>',
  "Clear rows":
    '<div class="fld"><label>Spreadsheet *</label><button class="btn sm" type="button">+ Spreadsheet</button></div>' +
    '<div class="fld" style="margin:0"><label>Sheet *</label><select class="isel" style="width:100%"><option></option></select></div>',
  "Get sheet contents":
    '<div class="fld"><label>Spreadsheet *</label><button class="btn sm" type="button">+ Spreadsheet</button></div>' +
    '<div class="fld" style="margin:0"><label>Sheet *</label><select class="isel" style="width:100%"><option></option></select></div>',
  // ---- Drive ----
  "Add email attachments to Drive":
    '<div class="fld"><label>Location for email attachments *</label><button class="btn sm" type="button">Drive</button></div>' +
    '<div class="fld" style="margin:0"><label>Gmail attachments *</label><button class="btn sm" type="button">+ Variables</button></div>',
  "Create a folder":
    '<div class="fld"><label>Folder name *</label><input type="text" placeholder="Enter @ to add variables"></div>' +
    '<div class="fld" style="margin:0"><label>Location for new folder</label><button class="btn sm" type="button">Drive</button></div>',
  "Move files or folders":
    '<div class="fld"><label>What to move *</label><button class="btn sm" type="button">+ Variables</button></div>' +
    '<div class="fld" style="margin:0"><label>Location to move to *</label><button class="btn sm" type="button">Drive</button></div>',
  "Copy a file or folder":
    '<label>What to copy</label><div class="seg" style="margin:0 0 0.7rem"><button type="button" class="on" onclick="sdrSegPick(this)">File</button><button type="button" onclick="sdrSegPick(this)">Folder</button></div>' +
    '<div class="fld"><label>Select file to copy *</label><button class="btn sm" type="button">Drive</button></div>' +
    '<div class="fld"><label>Destination folder *</label><button class="btn sm" type="button">Drive</button></div>' +
    '<div class="fld" style="margin:0"><label>Rename (optional)</label><input type="text" placeholder="Enter @ to add variables"></div>',
  "Search for a doc":
    '<div class="fld" style="margin:0"><label>Document name *</label><input type="text" placeholder="Enter @ to add variables"></div>',
  // ---- Calendar ----
  "Set up a meeting":
    '<div class="fld"><label>Title *</label><input type="text" placeholder="Enter @ to add variables"></div>' +
    '<div class="row"><div class="fld" style="margin:0"><label>Date *</label><input type="text" value="11/14/2026"></div><div class="fld" style="margin:0"><label>Time *</label><input type="text" value="11:52pm"></div></div>' +
    '<div class="fld"><label>Duration *</label><select class="isel" style="width:100%"><option>30 minutes</option><option>1 hour</option></select></div>' +
    '<div class="fld"><label>Repeat *</label><select class="isel" style="width:100%"><option>Does not repeat</option></select></div>' +
    '<div class="fld"><label>Guests</label><button class="btn sm" type="button">+ Variables</button></div>' +
    '<label class="check"><input type="checkbox"> Add a video conference to the meeting</label>' +
    '<div class="fld" style="margin:0.7rem 0 0"><label>Event description</label><textarea placeholder="Enter @ to add variables"></textarea></div>',
  "Add guests to a meeting":
    '<div class="fld"><label>Meeting *</label><button class="btn sm" type="button">+ Variables</button></div>' +
    '<div class="fld" style="margin:0"><label>Guests to add *</label><button class="btn sm" type="button">+ Variables</button></div>',
  // ---- Docs ----
  "Create a doc":
    '<div class="fld"><label>New doc name *</label><input type="text" placeholder="Enter @ to add variables"></div>' +
    '<div class="fld"><label>Content to add</label><textarea placeholder="Enter @ to add variables"></textarea></div>' +
    '<div class="fld" style="margin:0"><label>Location for new doc</label><button class="btn sm" type="button">Drive</button></div>',
  "Add to a doc":
    '<div class="fld"><label>Doc to update *</label><button class="btn sm" type="button">+ Document</button></div>' +
    '<div class="row"><div class="fld" style="margin:0"><label>Tab to update</label><select class="isel" style="width:100%"><option>First tab</option></select></div>' +
    '<div class="fld" style="margin:0"><label>Where to add</label><select class="isel" style="width:100%"><option>After existing content</option></select></div></div>' +
    '<div class="fld" style="margin:0"><label>Content to add *</label><textarea placeholder="Enter @ to add variables"></textarea></div>',
  // ---- Tasks ----
  "Create a task":
    '<div class="fld"><label>Task title *</label><input type="text" placeholder="Enter @ to add variables"></div>' +
    '<div class="fld"><label>Task details</label><textarea placeholder="Enter @ to add variables"></textarea></div>' +
    '<div class="fld" style="margin:0"><label>Date *</label><select class="isel" style="width:100%"><option>No date</option><option>Today</option></select></div>'
};

function sdrMakeStep(preset) {
  var wrap = document.getElementById("sdr-flowSteps");
  var id = "sdrst" + (Date.now() + Math.floor(Math.random() * 9999));
  var n = document.createElement("div");
  n.className = "node";
  n.style.marginBottom = "0.6rem";
  n.innerHTML =
    '<div class="nh"><span class="ntag nt-step">step</span>' +
    '<select class="isel" style="margin-left:auto"></select>' +
    '<span class="x" style="margin-left:0.5rem;cursor:pointer" onclick="sdrRemoveStep(this)">×</span></div>' +
    '<div id="' + id + '"></div>';
  wrap.appendChild(n);
  var sel = n.querySelector("select");
  sel.innerHTML = SDR_CATALOG;
  sel.addEventListener("change", function () { sdrPickStep(id, sel.value); });
  if (preset) {
    sel.value = preset;
    sdrPickStep(id, preset);
  }
}

function sdrAddStep() { sdrMakeStep(); }

function sdrRemoveStep(el) { el.closest(".node").remove(); }

function sdrPickStep(id, v) {
  document.getElementById(id).innerHTML =
    SDR_STEP_CFG[v] || (v ? '<div class="hint" style="margin:0.5rem 0 0">configure inputs — reference earlier steps output</div>' : "");
}

/* ---- Starter config (per-starter fields, from Studio) ---- */
var SDR_STARTER_CFG = {
  "On a schedule":
    '<div class="chips" style="margin-bottom:0.7rem"><span class="chip">Monday at 7:45 AM</span><span class="chip">Weekdays at 7:45 AM</span><span class="chip">In 5 minutes</span></div>' +
    '<div class="row"><div class="fld" style="margin:0"><label>Date *</label><input type="text" value="11/16/2026"></div><div class="fld" style="margin:0"><label>Time *</label><input type="text" value="7:45am"></div></div>' +
    '<div class="fld"><label>Repeat *</label><select class="isel" style="width:100%"><option>Does not repeat</option><option>Daily</option><option>Weekly</option></select></div>' +
    '<div class="fld" style="margin:0"><label>Ends</label><select class="isel" style="width:100%"><option>1 year</option><option>Never</option><option>On a date</option></select></div>',
  "Start manually":
    '<div class="hint" style="margin:0 0 0.6rem">Start on demand in Studio, @mention in Gemini, or as part of a skill. Specify the data the flow needs.</div>' +
    '<div class="rowline"><input type="text" placeholder="Data name *"><input type="text" placeholder="Default value"><span class="x">×</span></div>' +
    '<button class="addbtn" type="button">+ Add</button>',
  "When I get an email":
    '<div class="seg" style="margin-bottom:0.7rem"><button type="button" onclick="sdrSegPick(this)">All emails</button><button type="button" class="on" onclick="sdrSegPick(this)">Specific emails</button></div>' +
    '<div class="fld"><label>From</label><input type="text"></div>' +
    '<div class="fld"><label>Has the words</label><input type="text"><div class="hint" style="margin-top:0.35rem">Limit to emails with these words. Attachments aren\'t searched.</div></div>' +
    '<label class="check"><input type="checkbox"> Skip thread replies</label>',
  "When I get a chat message":
    '<div class="seg" style="margin-bottom:0.7rem"><button type="button" class="on" onclick="sdrSegPick(this)">From specific people</button><button type="button" onclick="sdrSegPick(this)">By conversation type</button></div>' +
    '<div class="fld" style="margin:0"><label>Senders *</label><input type="text"></div>',
  "When I'm mentioned":
    '<div class="seg" style="margin-bottom:0.7rem"><button type="button" onclick="sdrSegPick(this)">All @mentions</button><button type="button" class="on" onclick="sdrSegPick(this)">Specific @mentions</button></div>' +
    '<div class="fld"><label>People who mention me</label><input type="text"></div>' +
    '<div class="fld" style="margin:0"><label>Conversation type</label><select class="isel" style="width:100%"><option>All messages</option></select></div>',
  "When an emoji reaction is added":
    '<div class="hint" style="margin:0 0 0.6rem">Start when someone reacts with the selected emojis.</div>' +
    '<div class="fld" style="margin:0"><label>Reaction emojis *</label><button class="btn sm" type="button">Emoji</button></div>',
  "When a sheet changes":
    '<div class="fld"><label>Spreadsheet *</label><button class="btn sm" type="button">+ Spreadsheet</button></div>' +
    '<div class="fld" style="margin:0"><label>Sheet *</label><select class="isel" style="width:100%"><option></option></select></div>',
  "When an item is added to a folder":
    '<div class="hint" style="margin:0 0 0.6rem">Starts when a file or folder is added to the selected folder or its subfolders.</div>' +
    '<div class="fld"><label>Folder *</label><button class="btn sm" type="button">Drive</button></div>' +
    '<label class="check"><input type="checkbox"> Allow start when other flows add Gmail attachments to Drive</label>',
  "When a file is edited":
    '<div class="hint" style="margin:0 0 0.6rem">Starts when a file is edited, but not for suggestions, comments, or renames.</div>' +
    '<div class="fld" style="margin:0"><label>File *</label><button class="btn sm" type="button">Drive</button></div>',
  "When an item in a folder is edited":
    '<div class="hint" style="margin:0 0 0.6rem">Starts when any file in a folder (or subfolders) is edited; not for suggestions, comments, renames.</div>' +
    '<div class="fld" style="margin:0"><label>Folder *</label><button class="btn sm" type="button">Drive — Trigger folder …CNcrFxldG</button></div>',
  "Based on a meeting":
    '<div class="fld"><label>Meeting *</label><input type="text"></div>' +
    '<div class="row"><div class="fld" style="margin:0"><label>Time offset *</label><input type="text" value="10"></div><div class="fld" style="margin:0"><label>Units *</label><select class="isel" style="width:100%"><option>Minutes</option><option>Hours</option></select></div></div>' +
    '<div class="fld" style="margin:0"><label>Start *</label><select class="isel" style="width:100%"><option>Before meeting</option><option>After meeting</option></select></div>',
  "When meeting outputs are ready":
    '<label>Start when ready</label><div class="seg" style="margin:0 0 0.7rem"><button type="button" class="on" onclick="sdrSegPick(this)">Transcript</button><button type="button" onclick="sdrSegPick(this)">Notes by Gemini</button></div>' +
    '<label>Meeting type</label><div class="seg" style="margin:0"><button type="button" class="on" onclick="sdrSegPick(this)">All meetings</button><button type="button" onclick="sdrSegPick(this)">You organized</button><button type="button" onclick="sdrSegPick(this)">Specific</button></div>',
  "When a form response comes in":
    '<div class="fld"><label>Form *</label><button class="btn sm" type="button">+ Form</button></div>' +
    '<label class="check"><input type="checkbox"> Also run when a form response is edited</label>'
};

function sdrPickStarter(v) {
  document.getElementById("sdr-starterCfg").innerHTML = SDR_STARTER_CFG[v] || "";
}

document.addEventListener("DOMContentLoaded", function () {
  if (!document.querySelector(".sdr")) return;
  document.querySelectorAll(".sdr .tabs button[data-sdr-tab]").forEach(function (t) {
    t.addEventListener("click", function () { sdrGo(t.dataset.sdrTab); });
  });
  document.querySelectorAll(".sdr .chip").forEach(function (c) {
    c.addEventListener("click", function () { c.classList.toggle("sel"); });
  });
  // Seed the flow with its current two steps — both editable / changeable.
  sdrMakeStep("Ask Gemini");
  sdrMakeStep("Add to a doc");
  sdrPickStarter("When an item in a folder is edited");
});
