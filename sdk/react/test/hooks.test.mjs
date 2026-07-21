import { test } from "node:test";
import assert from "node:assert/strict";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import {
  TrueFigureProvider,
  useTrueFigure,
  asyncReducer,
  initialAsyncState,
  TrueFigureClient,
} from "../dist/index.js";

test("asyncReducer models the full load state machine", () => {
  let s = initialAsyncState();
  assert.deepEqual(s, { data: null, error: null, loading: true });
  s = asyncReducer(s, { type: "start" });
  assert.deepEqual(s, { data: null, error: null, loading: true });
  s = asyncReducer(s, { type: "success", data: { figures: [] } });
  assert.deepEqual(s, { data: { figures: [] }, error: null, loading: false });
  // a later refetch that fails clears data and surfaces the error
  s = asyncReducer(s, { type: "start" });
  assert.equal(s.loading, true);
  const err = new Error("boom");
  s = asyncReducer(s, { type: "failure", error: err });
  assert.deepEqual(s, { data: null, error: err, loading: false });
});

test("Provider supplies the thin client to the tree via context", () => {
  function Probe() {
    const client = useTrueFigure();
    assert.ok(client instanceof TrueFigureClient);
    return React.createElement("span", null, client.baseUrl);
  }
  const markup = renderToStaticMarkup(
    React.createElement(
      TrueFigureProvider,
      { apiKey: "tf_read_scoped", baseUrl: "https://api.example.test" },
      React.createElement(Probe),
    ),
  );
  assert.equal(markup, "<span>https://api.example.test</span>");
});

test("Provider accepts a pre-built client", () => {
  const client = new TrueFigureClient("k", { baseUrl: "https://prebuilt.test", deploymentId: "dep-9" });
  function Probe() {
    const c = useTrueFigure();
    assert.equal(c, client);
    return React.createElement("span", null, c.deploymentId);
  }
  const markup = renderToStaticMarkup(
    React.createElement(TrueFigureProvider, { apiKey: "ignored", client }, React.createElement(Probe)),
  );
  assert.equal(markup, "<span>dep-9</span>");
});

test("useTrueFigure throws outside a provider", () => {
  function Bare() {
    useTrueFigure();
    return null;
  }
  assert.throws(() => renderToStaticMarkup(React.createElement(Bare)), /within a <TrueFigureProvider>/);
});
