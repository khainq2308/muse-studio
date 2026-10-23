import { describe, expect, it } from "vitest";
import {
  NODE_HEIGHT,
  MIN_READABLE_ZOOM,
  NODE_WIDTH,
  VIEWPORT_PADDING_X,
  buildFlowNodes,
  gridPosition,
  hasCustomLayout,
  planAutoLayout,
  viewportForNodes,
  type WorkflowCreateOut,
} from "@/features/workflow-canvas/components/flow-model";

const M3_IDS = [
  "n_script_in",
  "n_analyze",
  "n_bible",
  "n_scenes",
  "n_shots",
  "n_prompt",
  "n_approval",
];

function workflow(uiLayout: WorkflowCreateOut["ui_layout"]): WorkflowCreateOut {
  return {
    id: "wf",
    project_id: "p",
    name: "wf",
    current_version: 1,
    execution_order: M3_IDS,
    cache_keys: {},
    nodes: M3_IDS.map((node_id) => ({ node_id, node_type: "ScriptInput" })),
    edges: [],
    ui_layout: uiLayout,
  };
}

const backendDefault = Object.fromEntries(M3_IDS.map((id, idx) => [id, { x: idx * 240, y: 120 }]));

describe("hasCustomLayout", () => {
  it("treats an empty layout and the backend single-row default as not custom", () => {
    expect(hasCustomLayout(workflow({}))).toBe(false);
    expect(hasCustomLayout(workflow(backendDefault))).toBe(false);
  });

  it("honors a saved layout that spans more than one row", () => {
    expect(hasCustomLayout(workflow({ ...backendDefault, n_bible: { x: 40, y: 300 } }))).toBe(true);
  });
});

describe("buildFlowNodes", () => {
  it("wraps the default single row onto a grid with the requested column count", () => {
    const positions = buildFlowNodes(workflow(backendDefault), 3).map((node) => node.position);
    expect(new Set(positions.map((position) => position.y)).size).toBe(3);
    expect(positions[3]).toEqual(gridPosition(3, 3));
    expect(positions[3]?.x).toBe(0);
  });

  it("keeps custom saved positions untouched", () => {
    const custom = { ...backendDefault, n_bible: { x: 40, y: 300 } };
    const positions = buildFlowNodes(workflow(custom), 2).map((node) => node.position);
    expect(positions[2]).toEqual({ x: 40, y: 300 });
    expect(positions[6]).toEqual({ x: 1440, y: 120 });
  });
});

describe("planAutoLayout", () => {
  it("fits all seven nodes readably on a desktop-sized canvas", () => {
    const plan = planAutoLayout(7, 870, 500);
    expect(plan.mode).toBe("fit");
    expect(plan.columns).toBeGreaterThan(1);
  });

  it("anchors a readable single column at the top on a phone-sized canvas", () => {
    expect(planAutoLayout(7, 300, 420)).toEqual({ columns: 1, mode: "top" });
  });
});

describe("viewportForNodes", () => {
  const column = [0, 1, 2, 3, 4, 5, 6].map((index) => ({ position: gridPosition(index, 1) }));

  it("keeps nodes at a readable zoom and the first node visible in top mode", () => {
    const viewport = viewportForNodes(column, 300, 420, "top", 0.3);
    expect(viewport).not.toBeNull();
    expect(viewport!.zoom).toBeGreaterThanOrEqual(MIN_READABLE_ZOOM);
    // Leaves a gutter for the bottom-left zoom controls.
    expect(viewport!.x).toBeGreaterThanOrEqual(VIEWPORT_PADDING_X);
    expect(viewport!.y).toBeGreaterThanOrEqual(0);
    // Horizontally the whole node fits inside the canvas.
    expect(viewport!.x).toBeGreaterThanOrEqual(0);
    expect(viewport!.x + NODE_WIDTH * viewport!.zoom).toBeLessThanOrEqual(300);
  });

  it("centers the whole graph in fit mode without zooming past 1", () => {
    const viewport = viewportForNodes([{ position: { x: 0, y: 0 } }], 800, 400, "fit", 0.3);
    expect(viewport).toEqual({ x: (800 - NODE_WIDTH) / 2, y: (400 - NODE_HEIGHT) / 2, zoom: 1 });
  });
});
