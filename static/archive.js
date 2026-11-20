const player = document.getElementById("player");
if (player) {
  const playbackError = document.getElementById("playback-error");
  const transcript = document.getElementById("transcript-lines");
  const lines = transcript ? Array.from(transcript.querySelectorAll("[data-start]")) : [];
  const starts = lines.map(line => Number(line.dataset.start));
  let active = -1;

  const follow = document.getElementById("follow");
  function updateActive() {
    let low = 0;
    let high = starts.length;
    while (low < high) {
      const middle = (low + high) >>> 1;
      if (starts[middle] <= player.currentTime) low = middle + 1;
      else high = middle;
    }
    const next = low - 1;
    if (next === active) return;
    lines[active]?.classList.remove("active");
    active = next;
    const line = lines[active];
    line?.classList.add("active");
    if (line && !line.hidden && follow?.getAttribute("aria-pressed") === "true") {
      transcript.scrollTop += line.getBoundingClientRect().top - transcript.getBoundingClientRect().top;
    }
  }

  function seekFromHash() {
    const match = /^#t=(\d+(?:\.\d+)?(?:e[+-]?\d+)?)$/i.exec(location.hash);
    if (match && player.readyState > 0) player.currentTime = Number(match[1]);
    updateActive();
  }

  player.addEventListener("loadedmetadata", seekFromHash);
  player.addEventListener("timeupdate", updateActive);
  player.addEventListener("error", () => { playbackError.hidden = false; });
  if (player.error) playbackError.hidden = false;
  window.addEventListener("hashchange", seekFromHash);
  seekFromHash();

  if (transcript) {
    const tools = document.getElementById("transcript-tools");
    const query = document.getElementById("transcript-query");
    const count = document.getElementById("transcript-count");
    const text = lines.map(line => line.lastElementChild.textContent.toLocaleLowerCase());
    tools.hidden = false;
    follow.hidden = false;

    function filterTranscript() {
      const term = query.value.trim().toLocaleLowerCase();
      let visible = 0;
      lines.forEach((line, index) => {
        line.hidden = !text[index].includes(term);
        if (!line.hidden) visible += 1;
      });
      count.textContent = term ? `${visible} matching ${visible === 1 ? "segment" : "segments"}` : "";
    }

    query.addEventListener("input", filterTranscript);
    follow.addEventListener("click", () => {
      const enabled = follow.getAttribute("aria-pressed") !== "true";
      follow.setAttribute("aria-pressed", String(enabled));
      if (enabled) {
        active = -1;
        updateActive();
      }
    });
    transcript.addEventListener("click", event => {
      const link = event.target.closest('a[href^="#t="]');
      if (!link) return;
      event.preventDefault();
      history.replaceState(null, "", link.hash);
      seekFromHash();
      player.scrollIntoView({ block: "nearest" });
    });
    filterTranscript();
  }
}
