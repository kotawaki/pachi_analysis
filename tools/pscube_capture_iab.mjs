import fs from "node:fs/promises";
import path from "node:path";

export const DEFAULT_BASE_URL =
  "https://www.pscube.jp/dedamajyoho-P-townDMMpachi/c713848/cgi-bin/nc-v06-001.php";

function zfillMachine(machine) {
  const text = String(machine).trim();
  return text.length >= 4 ? text : text.padStart(4, "0");
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function ensureDir(dir) {
  await fs.mkdir(dir, { recursive: true });
}

async function saveText(filePath, text) {
  await ensureDir(path.dirname(filePath));
  await fs.writeFile(filePath, text, "utf8");
}

async function saveBytes(filePath, bytes) {
  await ensureDir(path.dirname(filePath));
  await fs.writeFile(filePath, bytes);
}

function retryDeadlineToday(hour, minute = 0) {
  const now = new Date();
  const deadline = new Date(now);
  deadline.setHours(hour, minute, 0, 0);
  return deadline;
}

function beforeDeadline(deadline) {
  return !deadline || Date.now() < deadline.getTime();
}

async function scrollChartIntoView(tab) {
  await tab.playwright.evaluate(
    () => {
      const el = document.querySelector("#divCHART") || document.querySelector('[id^="CHART-"]');
      el?.scrollIntoView({ block: "start" });
    },
    undefined,
    { timeoutMs: 10000 }
  );
}

export async function inspectChartSvg(tab, ymd) {
  return await tab.playwright.evaluate(
    (targetYmd) => {
      const chart = document.getElementById(`CHART-${targetYmd}`);
      const root = chart?.closest("ul") || chart?.parentElement || document;
      const graphStroke = root.querySelectorAll(".amcharts-graph-stroke").length;
      const zeroGrid = root.querySelectorAll(".amcharts-axis-zero-grid").length;
      const plotArea = root.querySelectorAll(".amcharts-plot-area").length;
      const svg = root.querySelectorAll("svg").length;
      return {
        ymd: targetYmd,
        hasChart: !!chart,
        svg,
        graphStroke,
        zeroGrid,
        plotArea,
        ready: !!chart && graphStroke > 0 && zeroGrid > 0 && plotArea > 0,
      };
    },
    ymd,
    { timeoutMs: 10000 }
  );
}

export async function waitForChartSvg(tab, ymd, options = {}) {
  const timeoutMs = options.timeoutMs ?? 20000;
  const pollMs = options.pollMs ?? 1000;
  const started = Date.now();
  let last = null;

  await scrollChartIntoView(tab);
  while (Date.now() - started <= timeoutMs) {
    last = await inspectChartSvg(tab, ymd);
    if (last.ready) {
      return last;
    }
    await tab.playwright.waitForTimeout(pollMs);
  }
  return last || { ymd, ready: false };
}

export async function inspectMachinePage(tab) {
  return await tab.playwright.evaluate(
    () => {
      const selected =
        document.querySelector("#YMD-ul li.selected") ||
        document.querySelector("#YMD-ul li");
      const ymd = selected?.getAttribute("data-ymd") || "";
      const dai = (document.querySelector("#divDAI h2")?.textContent || "")
        .replace(/[^\d]/g, "")
        .padStart(4, "0");
      const summaryRows = [...document.querySelectorAll(".grid-ex tr")].map((tr) =>
        [...tr.querySelectorAll("td")].map((td) => td.textContent.trim())
      );
      const todaySummary = summaryRows.find((row) => row.some((cell) => cell === "本日")) || [];
      const numbers = todaySummary
        .map((cell) => String(cell).trim())
        .filter((cell) => /^\d+$/.test(cell))
        .map((cell) => Number(cell));
      const bodyText = (document.body?.innerText || "").replace(/\s+/g, " ");
      const bodyBonus = bodyText.match(/本日\s+(\d+)\s+\d+\s+1\//);
      const bonusCount = numbers.length ? numbers[0] : bodyBonus ? Number(bodyBonus[1]) : null;
      const historyRows = [...document.querySelectorAll("#tblHISTb tr")].map((tr) =>
        [...tr.querySelectorAll("td")].map((td) => td.textContent.trim())
      );
      const hasSlotReg = historyRows.some((row) => row[3] === "REG");
      const initials = hasSlotReg
        ? historyRows.filter((row) => row[3] === "REG" && /^\d+$/.test(row[2] || "") && Number(row[2]) >= 10)
        : historyRows.filter((row) => row[3] === "初当り");
      const continuationRegs = hasSlotReg
        ? historyRows.filter((row) => row[3] === "REG" && /^\d+$/.test(row[2] || "") && Number(row[2]) < 10)
        : [];
      const more = document.querySelector("#tblHISTm");
      return {
        title: document.title,
        url: location.href,
        ymd,
        machine: dai,
        historyKind: hasSlotReg ? "slot_reg" : "pachinko_initial",
        bonusCount,
        historyCount: historyRows.length,
        initialCount: initials.length,
        initialTimes: initials.map((row) => row[1]),
        continuationRegCount: continuationRegs.length,
        continuationRegTimes: continuationRegs.map((row) => row[1]),
        moreVisible: more ? !!(more.offsetWidth || more.offsetHeight || more.getClientRects().length) : false,
      };
    },
    undefined,
    { timeoutMs: 10000 }
  );
}

export async function expandHistory(tab, options = {}) {
  const waitMs = options.waitMs ?? 3500;
  const maxClicks = options.maxClicks ?? 8;
  const stableRounds = options.stableRounds ?? 1;
  const snapshots = [];
  let stable = 0;

  for (let click = 0; click <= maxClicks; click++) {
    const state = await inspectMachinePage(tab);
    snapshots.push({ click, ...state });

    if (
      state.historyKind !== "slot_reg" &&
      state.bonusCount != null &&
      state.historyCount >= state.bonusCount
    ) {
      break;
    }
    if (!state.moreVisible) {
      break;
    }
    if (click === maxClicks) {
      break;
    }

    const beforeCount = state.historyCount;
    await tab.playwright.evaluate(
      () => {
        document.querySelector("#tblHISTm")?.scrollIntoView({ block: "center" });
      },
      undefined,
      { timeoutMs: 10000 }
    );
    await tab.playwright.waitForTimeout(500);
    await tab.playwright.locator("#tblHISTm").click({});
    await tab.playwright.waitForTimeout(waitMs);

    const after = await inspectMachinePage(tab);
    if (after.historyCount <= beforeCount) {
      stable += 1;
    } else {
      stable = 0;
    }
    if (stable >= stableRounds) {
      snapshots.push({ click: click + 1, ...after });
      break;
    }
  }

  return snapshots;
}

export async function captureMachine(tab, browser, options) {
  const machine = zfillMachine(options.machine);
  const url = `${options.baseUrl || DEFAULT_BASE_URL}?cd_dai=${machine}`;
  const outRoot = options.outRoot;
  const openWaitMs = options.openWaitMs ?? 10000;
  const chartScrollY = options.chartScrollY ?? 3400;
  const viewport = options.viewport || { width: 590, height: 1000 };

  if (!outRoot) {
    throw new Error("outRoot is required");
  }

  const viewportCap = await browser.capabilities.get("viewport");
  await viewportCap.set(viewport);

  try {
    await tab.goto(url);
  } catch (error) {
    const currentUrl = (await tab.url()) || "";
    if (!currentUrl.includes(`cd_dai=${machine}`)) {
      throw error;
    }
  }
  await tab.playwright.waitForLoadState({ state: "domcontentloaded", timeoutMs: 30000 });
  await tab.playwright.waitForTimeout(openWaitMs);

  const expansion = await expandHistory(tab, {
    waitMs: options.moreWaitMs ?? 3500,
    maxClicks: options.maxMoreClicks ?? 8,
    stableRounds: options.stableRounds ?? 1,
  });
  const info = await inspectMachinePage(tab);
  const ymd = options.date || info.ymd;
  const chartSvg = options.requireChartSvg === false
    ? { ymd, ready: true, skipped: true }
    : await waitForChartSvg(tab, ymd, {
        timeoutMs: options.chartSvgWaitMs ?? 20000,
        pollMs: options.chartSvgPollMs ?? 1000,
      });

  const htmlPath = path.join(outRoot, "html", `${ymd}_${machine}.html`);
  const dom = await tab.playwright.evaluate(
    () => "<!doctype html>\n" + document.documentElement.outerHTML,
    undefined,
    { timeoutMs: 10000 }
  );
  await saveText(htmlPath, dom);

  let chartPath = null;
  if (options.captureChart !== false) {
    const chartSelector = options.chartSelector ?? "#divCHART";
    const useChartClip = options.chartClip === true;
    let clip = null;
    if (useChartClip && chartSelector) {
      clip = await tab.playwright.evaluate(
        ({ selector, clipWidth, clipHeight }) => {
          const candidates = [...document.querySelectorAll(selector)];
          const el =
            candidates.find((candidate) => {
              const text = (candidate.textContent || "").replace(/\s+/g, "");
              const r = candidate.getBoundingClientRect();
              return (
                r.width > 0 &&
                r.height > 0 &&
                text.includes("09:00") &&
                text.includes("21:00")
              );
            }) || candidates.find((candidate) => {
              const r = candidate.getBoundingClientRect();
              return r.width > 0 && r.height > 0;
            });
          if (!el) return null;
          el.scrollIntoView({ block: "start" });
          const r = el.getBoundingClientRect();
          return {
            x: Math.max(0, r.left),
            y: Math.max(0, r.top),
            width: Math.min(clipWidth || r.width, r.width),
            height: Math.min(clipHeight || r.height, r.height),
          };
        },
        {
          selector: chartSelector,
          clipWidth: options.chartClipWidth ?? viewport.width,
          clipHeight: options.chartClipHeight,
        },
        { timeoutMs: 10000 }
      );
    }
    if (!clip) {
      const aligned = await tab.playwright.evaluate(
        ({ selector, fallbackScrollY }) => {
          const candidates = [...document.querySelectorAll(selector)];
          const el =
            candidates.find((candidate) => {
              const text = (candidate.textContent || "").replace(/\s+/g, "");
              const r = candidate.getBoundingClientRect();
              return (
                r.width > 0 &&
                r.height > 0 &&
                text.includes("09:00") &&
                text.includes("21:00")
              );
            }) || candidates.find((candidate) => {
              const r = candidate.getBoundingClientRect();
              return r.width > 0 && r.height > 0;
            });
          if (el) {
            el.scrollIntoView({ block: "start" });
            return true;
          }
          window.scrollTo(0, fallbackScrollY);
          return false;
        },
        { selector: chartSelector, fallbackScrollY: chartScrollY },
        { timeoutMs: 10000 }
      );
      if (!aligned) {
        await tab.playwright.waitForTimeout(300);
      }
    }
    await tab.playwright.waitForTimeout(options.chartWaitMs ?? 1000);
    chartPath = path.join(outRoot, "chart", `${ymd}_${machine}_chart.png`);
    const png = clip
      ? await tab.screenshot({ fullPage: false, clip })
      : await tab.screenshot({ fullPage: false });
    await saveBytes(chartPath, png);
  }

  return {
    machine,
    ymd,
    url,
    htmlPath,
    chartPath,
    expansion,
    final: await inspectMachinePage(tab),
    chartSvg,
  };
}

export async function captureMachines(tab, browser, options) {
  const results = [];
  const machines = options.machines || [];
  const delayMs = options.delayMs ?? 10000;
  const maxSvgRetries = options.maxSvgRetries ?? 2;
  const retryDelayMs = options.svgRetryDelayMs ?? 30000;
  const retryUntilHour = options.svgRetryUntilHour ?? 10;
  const retryUntilMinute = options.svgRetryUntilMinute ?? 0;
  const retryDeadline = options.svgRetryUntil
    ? new Date(options.svgRetryUntil)
    : retryDeadlineToday(retryUntilHour, retryUntilMinute);

  for (let i = 0; i < machines.length; i++) {
    let result = await captureMachine(tab, browser, {
      ...options,
      machine: machines[i],
    });
    let retry = 0;
    while (
      result.chartSvg &&
      !result.chartSvg.ready &&
      retry < maxSvgRetries &&
      beforeDeadline(retryDeadline)
    ) {
      retry += 1;
      console.warn(
        `[svg-missing] ${result.ymd}_${result.machine} retry ${retry}/${maxSvgRetries}`,
        result.chartSvg
      );
      await sleep(retryDelayMs);
      result = await captureMachine(tab, browser, {
        ...options,
        machine: machines[i],
      });
    }
    result.svgRetryCount = retry;
    result.svgRetryDeadline = retryDeadline.toISOString();
    results.push(result);
    if (i < machines.length - 1 && delayMs > 0) {
      await sleep(delayMs);
    }
  }

  return results;
}
