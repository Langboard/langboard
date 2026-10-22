import assert from "node:assert/strict";
import test from "node:test";
import { boardTouchAutoScrollDelta, startBoardTouchAutoScroll } from "./BoardTouchAutoScroll.ts";

test("long boards scroll only while the touch pointer is inside an edge zone", () => {
    assert.equal(boardTouchAutoScrollDelta(200, 0, 400), 0);
    assert.equal(boardTouchAutoScrollDelta(72, 0, 400), 0);
    assert.equal(boardTouchAutoScrollDelta(328, 0, 400), 0);
    assert.ok(boardTouchAutoScrollDelta(20, 0, 400) < 0);
    assert.ok(boardTouchAutoScrollDelta(380, 0, 400) > 0);
});

test("edge scrolling is bounded and accelerates toward either edge", () => {
    const nearLeft = boardTouchAutoScrollDelta(60, 0, 400);
    const farLeft = boardTouchAutoScrollDelta(0, 0, 400);
    const nearRight = boardTouchAutoScrollDelta(340, 0, 400);
    const farRight = boardTouchAutoScrollDelta(400, 0, 400);
    assert.ok(Math.abs(farLeft) > Math.abs(nearLeft));
    assert.ok(Math.abs(farRight) > Math.abs(nearRight));
    assert.equal(farLeft, -18);
    assert.equal(farRight, 18);
});

test("invalid or disabled viewports never scroll", () => {
    assert.equal(boardTouchAutoScrollDelta(100, 200, 100), 0);
    assert.equal(boardTouchAutoScrollDelta(0, 0, 400, 0), 0);
    assert.equal(boardTouchAutoScrollDelta(0, 0, 400, 72, 0), 0);
});

test("one frame scrolls independent horizontal and vertical owners, clamps bounds, and cancels both", (context) => {
    const originalWindow = globalThis.window;
    const frames = new Map<number, FrameRequestCallback>();
    let nextFrame = 0;
    globalThis.window = {
        requestAnimationFrame: (callback: FrameRequestCallback) => {
            frames.set(++nextFrame, callback);
            return nextFrame;
        },
        cancelAnimationFrame: (id: number) => {
            frames.delete(id);
        },
    } as unknown as Window & typeof globalThis;
    context.after(() => {
        globalThis.window = originalWindow;
    });
    const horizontal = {
        scrollLeft: 0,
        getBoundingClientRect: () => ({ left: 0, right: 400 }),
        scrollBy: ({ left = 0 }) => {
            horizontal.scrollLeft += left;
        },
    };
    const vertical = {
        scrollTop: 0,
        scrollHeight: 405,
        clientHeight: 400,
        getBoundingClientRect: () => ({ top: 0, bottom: 400 }),
        scrollBy: ({ top = 0 }) => {
            vertical.scrollTop += top;
        },
    };
    let pointerY = 400;
    let owner: typeof vertical | null = vertical;
    const stop = startBoardTouchAutoScroll(
        horizontal as unknown as HTMLElement,
        () => 400,
        () => (owner ? { scrollable: owner as unknown as HTMLElement, pointerY } : null)
    );
    const runFrame = () => {
        const [id, callback] = frames.entries().next().value!;
        frames.delete(id);
        callback(0);
    };
    runFrame();
    assert.equal(horizontal.scrollLeft, 18);
    assert.equal(vertical.scrollTop, 5);
    runFrame();
    assert.equal(vertical.scrollTop, 5);
    pointerY = 0;
    runFrame();
    assert.equal(vertical.scrollTop, 0);
    owner = null;
    runFrame();
    assert.equal(vertical.scrollTop, 0);
    const stale = frames.values().next().value!;
    stop();
    assert.equal(frames.size, 0);
    stale(0);
    assert.equal(frames.size, 0);
    assert.equal(horizontal.scrollLeft, 72);
});
