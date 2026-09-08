/**
 * <param-compare src="....json"></param-compare>
 *
 * 1本の動画に連結された「1パラメータの全段階」を、再生位置のジャンプで
 * 切り替えるビューア。
 *
 *   currentTime = (p * framesPerSegment + t) / fps
 *
 * 肝は、パラメータ p を変えてもセグメント内の時刻 t を保つこと。これにより
 * 常に「同じ瞬間の別の値」を比較できる。値ごとに別の <video> を切り替える
 * 方式では再生位置がずれてしまい、比較として成立しない。
 *
 * 依存ライブラリなし。
 */

const STYLE = `
:host {
  --pc-bg: #ffffff;
  --pc-fg: #1a1a1a;
  --pc-muted: #6b7280;
  --pc-border: #e5e7eb;
  --pc-accent: #2563eb;
  --pc-surface: #f9fafb;
  display: block;
  margin: 2rem 0;
  color: var(--pc-fg);
  font-family: inherit;
}
@media (prefers-color-scheme: dark) {
  :host {
    --pc-bg: #111317;
    --pc-fg: #e8eaed;
    --pc-muted: #9aa0a6;
    --pc-border: #2c2f36;
    --pc-accent: #6ea8fe;
    --pc-surface: #191c21;
  }
}
.wrap {
  border: 1px solid var(--pc-border);
  border-radius: 10px;
  overflow: hidden;
  background: var(--pc-surface);
}
.stage {
  position: relative;
  background: #000;
  aspect-ratio: var(--pc-aspect, 16 / 9);
}
video {
  display: block;
  width: 100%;
  height: 100%;
  object-fit: contain;
  background: #000;
}
.placeholder {
  position: absolute;
  inset: 0;
  display: grid;
  place-items: center;
  color: #9aa0a6;
  font-size: 0.85rem;
  background: rgba(0, 0, 0, 0.55);
}
/* hidden 属性は display:none を与えるが、上の display:grid の方が
   優先度が高く打ち消されてしまう。明示的に潰す。 */
.placeholder[hidden] { display: none; }
.controls { padding: 0.85rem 1rem 1rem; }
.row {
  display: flex;
  align-items: center;
  gap: 0.75rem;
}
.row + .row { margin-top: 0.7rem; }
.label {
  font-size: 0.8rem;
  color: var(--pc-muted);
  min-width: 3.5rem;
}
.value {
  font-variant-numeric: tabular-nums;
  font-weight: 600;
  font-size: 0.95rem;
  /* 固定幅にする。min-width だと値の桁数でスライダーの長さが変わる */
  flex: 0 0 6rem;
}
.badge {
  font-size: 0.68rem;
  padding: 0.1rem 0.4rem;
  border-radius: 999px;
  border: 1px solid var(--pc-border);
  color: var(--pc-muted);
  white-space: nowrap;
  flex: 0 0 auto;
}
/* デフォルト値のバッジは表示を切り替えても場所を空けたままにする。
   display で消すとスライダーの長さが値によって変わってしまう。 */
.default-badge { visibility: hidden; }
.default-badge.on { visibility: visible; }
.default-badge.none { display: none; }
input[type=range] {
  flex: 1;
  accent-color: var(--pc-accent);
  min-width: 0;
}
button {
  font: inherit;
  font-size: 0.85rem;
  padding: 0.25rem 0.7rem;
  border: 1px solid var(--pc-border);
  background: var(--pc-bg);
  color: var(--pc-fg);
  border-radius: 6px;
  cursor: pointer;
}
button:hover { border-color: var(--pc-accent); }
.ticks {
  display: flex;
  justify-content: space-between;
  font-size: 0.7rem;
  color: var(--pc-muted);
  font-variant-numeric: tabular-nums;
  margin-top: 0.15rem;
  padding-left: 4.25rem;
}
.note {
  margin-top: 0.6rem;
  font-size: 0.75rem;
  color: var(--pc-muted);
  line-height: 1.5;
}
.err {
  padding: 1rem;
  font-size: 0.85rem;
  color: #b91c1c;
}
.loading {
  padding: 1rem;
  font-size: 0.85rem;
  color: var(--pc-muted);
}
`;

function formatValue(v) {
  if (typeof v !== "number") return String(v);
  if (v === 0) return "0";
  const abs = Math.abs(v);
  if (abs >= 1e4 || abs < 1e-3) {
    // 1e+06 ではなく 1e6 と読ませる
    return v.toExponential(0).replace("e+", "e").replace("e-0", "e-");
  }
  return String(parseFloat(v.toPrecision(6)));
}

class ParamCompare extends HTMLElement {
  connectedCallback() {
    if (this._init) return;
    this._init = true;
    this.attachShadow({ mode: "open" });
    this.p = 0;
    this.t = 0;
    this._loaded = false;
    this._render();
    this._load();
  }

  disconnectedCallback() {
    if (this._observer) this._observer.disconnect();
  }

  _render() {
    const style = document.createElement("style");
    style.textContent = STYLE;
    this.shadowRoot.append(style);
    this._root = document.createElement("div");
    this._root.className = "wrap";
    this._root.innerHTML = `<div class="loading">メタデータを読み込み中...</div>`;
    this.shadowRoot.append(this._root);
  }

  async _load() {
    const src = this.getAttribute("src");
    if (!src) {
      this._fail("src 属性がありません");
      return;
    }
    let meta;
    try {
      const res = await fetch(src);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      meta = await res.json();
    } catch (e) {
      this._fail(`メタデータを読み込めません (${src}): ${e.message}`);
      return;
    }
    this.meta = meta;
    this.p = Number.isInteger(meta.default_index) ? meta.default_index : 0;
    this._build();
  }

  _fail(msg) {
    this._root.innerHTML = `<div class="err">${msg}</div>`;
  }

  _build() {
    const m = this.meta;
    const videoUrl = new URL(m.src, new URL(this.getAttribute("src"), location.href)).href;

    this._root.style.setProperty("--pc-aspect", `${m.width} / ${m.height}`);
    this._root.innerHTML = `
      <div class="stage">
        <video muted playsinline preload="none"></video>
        <div class="placeholder">動画を読み込み中...</div>
      </div>
      <div class="controls">
        <div class="row">
          <span class="label">${m.label || m.parm}</span>
          <input type="range" class="pslider" min="0" max="${m.values.length - 1}" step="1" value="${this.p}">
          <span class="value"></span>
          <span class="badge default-badge">default</span>
        </div>
        <div class="ticks"></div>
        <div class="row">
          <button class="play">一時停止</button>
          <input type="range" class="tslider" min="0" max="${m.frames_per_segment - 1}" step="1" value="0">
          <span class="badge frame"></span>
        </div>
        <div class="note"></div>
      </div>
    `;

    this.video = this._root.querySelector("video");
    this.pslider = this._root.querySelector(".pslider");
    this.tslider = this._root.querySelector(".tslider");
    this.valueEl = this._root.querySelector(".value");
    this.frameEl = this._root.querySelector(".frame");
    this.badgeEl = this._root.querySelector(".default-badge");
    this.playBtn = this._root.querySelector(".play");
    this.placeholder = this._root.querySelector(".placeholder");
    this._videoUrl = videoUrl;

    this._root.querySelector(".ticks").innerHTML = m.values
      .map((v) => `<span>${formatValue(v)}</span>`)
      .join("");

    const notes = [];
    if (m.camera && m.camera !== "CAM_main") {
      notes.push(
        "このパラメータはスケールが大きく変わるため、他の比較とは<strong>カメラ距離を変えて</strong>撮影しています。"
      );
    }
    notes.push("←→ で値、[ ] でコマ送り、Space で再生／停止。");
    this._root.querySelector(".note").innerHTML = notes.join("<br>");

    this._bind();
    this._updateUI();
    this._observe();
  }

  _bind() {
    this.pslider.addEventListener("input", () => {
      this.setParam(Number(this.pslider.value));
    });
    this.tslider.addEventListener("input", () => {
      this.setFrame(Number(this.tslider.value));
    });
    this.playBtn.addEventListener("click", () => this.toggle());

    this.setAttribute("tabindex", this.getAttribute("tabindex") ?? "0");
    this.addEventListener("keydown", (e) => {
      const keys = ["ArrowLeft", "ArrowRight", "[", "]", " "];
      if (!keys.includes(e.key)) return;
      e.preventDefault();
      if (e.key === "ArrowLeft") this.setParam(this.p - 1);
      else if (e.key === "ArrowRight") this.setParam(this.p + 1);
      else if (e.key === "[") this.step(-1);
      else if (e.key === "]") this.step(1);
      else if (e.key === " ") this.toggle();
    });
  }

  /** 画面内に入ったら読み込んで再生、外れたら止める。CPU と帯域の両方を節約する。 */
  _observe() {
    this._observer = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          if (entry.isIntersecting) {
            if (!this._loaded) {
              this._loaded = true;
              this.video.addEventListener(
                "loadeddata",
                () => {
                  this.placeholder.hidden = true;
                  this.video.currentTime = this._time();
                  this._startLoop();
                  this.video.play().catch(() => {});
                },
                { once: true }
              );
              this.video.addEventListener("error", () => {
                const err = this.video.error;
                this.placeholder.textContent =
                  `動画を読み込めませんでした (${err ? err.code : "?"})`;
              });
              // 最終セグメントを再生していると動画そのものの終端に達する。
              // ended になると再生が止まり requestVideoFrameCallback も
              // 呼ばれなくなるため、_sync による巻き戻しが走らない。
              // ここで明示的にセグメント先頭へ戻して再生を続ける。
              this.video.addEventListener("ended", () => {
                this.t = 0;
                this.video.currentTime = this._time();
                this._updateFrameUI();
                this.video.play().catch(() => {});
              });
              // preload="none" のままだと src を設定しても読み込みが始まらず
              // loadeddata が発火しない。画面内に入った今、明示的に読ませる。
              this.video.preload = "auto";
              this.video.src = this._videoUrl;
              this.video.load();
            } else if (this._wasPlaying !== false) {
              this.video.play().catch(() => {});
            }
          } else if (this._loaded) {
            this._wasPlaying = !this.video.paused;
            this.video.pause();
          }
        }
      },
      { rootMargin: "200px" }
    );
    this._observer.observe(this);
  }

  get T() {
    return this.meta.frames_per_segment;
  }

  _time(p = this.p, t = this.t) {
    // セグメント境界ちょうどを指すと、丸めで手前のセグメント末尾に
    // 落ちることがあるため、半フレーム分だけ内側を指す
    return (p * this.T + t + 0.5) / this.meta.fps;
  }

  setParam(p) {
    p = Math.max(0, Math.min(this.meta.values.length - 1, p));
    if (p === this.p) return;
    this.p = p;
    // t を保ったまま p だけ動かす — これが比較を成立させている
    this.video.currentTime = this._time();
    this._updateUI();
  }

  setFrame(t) {
    this.t = Math.max(0, Math.min(this.T - 1, t));
    this.video.currentTime = this._time();
    this._updateUI();
  }

  step(delta) {
    if (!this.video.paused) this.video.pause();
    this.setFrame((this.t + delta + this.T) % this.T);
    this._syncPlayBtn();
  }

  toggle() {
    if (this.video.paused) this.video.play().catch(() => {});
    else this.video.pause();
    this._syncPlayBtn();
  }

  _syncPlayBtn() {
    this.playBtn.textContent = this.video.paused ? "再生" : "一時停止";
  }

  _startLoop() {
    const tick = () => {
      this._sync();
      if (this.video.requestVideoFrameCallback) {
        this.video.requestVideoFrameCallback(tick);
      } else {
        requestAnimationFrame(tick);
      }
    };
    if (this.video.requestVideoFrameCallback) {
      this.video.requestVideoFrameCallback(tick);
    } else {
      requestAnimationFrame(tick);
    }
  }

  /** 再生位置を読み、セグメント終端に達したら先頭へ巻き戻す。
   *
   * フレーム番号ではなく秒で判定する。ブラウザによって currentTime が
   * 要求値のままだったり表示フレームの開始時刻に丸められたりするため、
   * フレーム番号で厳密に比較すると環境ごとに壊れる。
   *
   * 手前に落ちた場合に巻き戻さないのが重要。巻き戻す→また手前に落ちる、
   * を繰り返して1フレームで振動し、再生が進まなくなる。
   */
  _sync() {
    if (this.video.seeking) return;
    const fps = this.meta.fps;
    const T = this.T;
    const segStart = (this.p * T) / fps;
    const segEnd = ((this.p + 1) * T) / fps;
    const ct = this.video.currentTime;
    const eps = 1 / (fps * 4);

    if (ct >= segEnd - eps) {
      // 末尾に到達 → セグメント先頭へループ
      this.t = 0;
      this.video.currentTime = this._time();
      this._updateFrameUI();
      return;
    }

    if (ct < segStart) {
      // シークの丸めで手前に落ちた。半セグメント以上ずれている場合だけ
      // 引き戻し、わずかなずれは再生に任せる（振動を防ぐ）。
      if (segStart - ct > T / (2 * fps)) {
        this.t = 0;
        this.video.currentTime = this._time();
        this._updateFrameUI();
      }
      return;
    }

    const local = Math.min(T - 1, Math.floor((ct - segStart) * fps));
    if (local !== this.t) {
      this.t = local;
      this._updateFrameUI();
    }
  }

  _updateUI() {
    const m = this.meta;
    this.pslider.value = String(this.p);
    this.valueEl.textContent = formatValue(m.values[this.p]);
    if (!Number.isInteger(m.default_index)) {
      this.badgeEl.classList.add("none");
    } else {
      this.badgeEl.classList.toggle("on", this.p === m.default_index);
    }
    this._updateFrameUI();
    this._syncPlayBtn();
  }

  _updateFrameUI() {
    this.tslider.value = String(this.t);
    this.frameEl.textContent = `${this.t + 1} / ${this.T}`;
  }
}

customElements.define("param-compare", ParamCompare);
