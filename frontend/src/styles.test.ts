import { describe, expect, it } from "vitest";

import "./styles.css";

const styles = Array.from(document.styleSheets)
  .flatMap((sheet) => Array.from(sheet.cssRules))
  .map((rule) => rule.cssText)
  .join("\n");

describe("completed summary layout stylesheet", () => {
  it("keeps long completed content vertically scrollable", () => {
    expect(styles).toMatch(
      /\.speedometer\[data-lifecycle="completed"\]\s*\{[^}]*overflow-y:\s*auto/s,
    );
  });

  it("splits transcript and pace into desktop columns with a vertical divider", () => {
    expect(styles).toMatch(
      /\.summary-segment\s*\{[^}]*grid-template-columns:\s*minmax\(0, 1\.65fr\) minmax\(16rem, 1fr\)/s,
    );
    expect(styles).toMatch(
      /\.segment-analysis\s*\{[^}]*border-left:\s*1px solid var\(--line\)/s,
    );
  });

  it("stacks the regions with a horizontal divider on narrow screens", () => {
    expect(styles).toMatch(
      /@media \(max-width: 620px\)[\s\S]*\.summary-segment\s*\{\s*grid-template-columns:\s*1fr;\s*\}/,
    );
    expect(styles).toMatch(
      /@media \(max-width: 620px\)[\s\S]*\.segment-analysis\s*\{[^}]*border-top:\s*1px solid var\(--line\);\s*border-left:\s*0/s,
    );
  });
});
