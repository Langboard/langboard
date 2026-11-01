import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import test from "node:test";
import { runInNewContext } from "node:vm";
import { AxiosError, CanceledError } from "axios";
import i18next from "i18next";
import ts from "typescript";

for (const locale of ["en-US", "ko-KR", "ja-JP", "zh-CN"]) {
    test(`API error messages and callback behavior in ${locale}`, async (context) => {
        const errors = JSON.parse(readFileSync(new URL(`../../assets/locales/${locale}/errors.json`, import.meta.url), "utf8"));
        const i18n = i18next.createInstance();
        await i18n.init({ lng: locale, resources: { [locale]: { translation: { errors } } } });
        const require = createRequire(import.meta.url);
        const module = { exports: {} as typeof import("./setupApiErrorHandler") };
        const toasts: string[] = [];
        const loggedErrors: unknown[] = [];
        const source = readFileSync(new URL("./setupApiErrorHandler.ts", import.meta.url), "utf8");
        const javascript = ts.transpileModule(source, {
            compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, esModuleInterop: true },
        }).outputText;
        runInNewContext(javascript, {
            module,
            exports: module.exports,
            require: (name: string) => {
                if (name === "i18next") return { t: i18n.t.bind(i18n) };
                if (name === "@/components/base/Toast") return { Add: { error: (message: string) => toasts.push(message) } };
                if (name === "@langboard/core/utils") return { Utils: { Type: { isString: (value: unknown) => typeof value === "string" } } };
                if (name === "@langboard/core/enums") return { EHttpStatus: { HTTP_403_FORBIDDEN: 403 } };
                return require(name);
            },
            console: { error: (error: unknown) => loggedErrors.push(error) },
        });
        await context.test("default message handler preserves logging, localization, and cancellation", () => {
            const forbidden = new AxiosError("Private denial detail");
            forbidden.response = { status: 403, data: {} } as AxiosError["response"];
            const handler = module.exports.createApiErrorMessageHandler();
            assert.equal(handler(forbidden), errors.Forbidden);
            assert.equal(loggedErrors.pop(), forbidden);
            assert.equal(handler(new AxiosError("Private transport detail", AxiosError.ERR_NETWORK)), errors["Network error"]);
            assert.equal(handler(new Error("Private detail")), errors["Internal server error"]);
            assert.equal(handler(new CanceledError()), "");
            assert.deepEqual(toasts, []);
        });
        await context.test("code-free forbidden retains defaults with after-only override", () => {
            let redirects = 0;
            const error = new AxiosError("Private denial detail");
            error.response = { status: 403, data: {} } as AxiosError["response"];
            assert.equal(
                module.exports.createApiErrorMessageHandler(() => {
                    redirects++;
                })(error),
                errors.Forbidden
            );
            assert.equal(redirects, 1);
            assert.deepEqual(toasts, []);
        });
        await context.test("code-free forbidden without overrides uses status default", async () => {
            const error = new AxiosError("Private denial detail");
            error.response = { status: 403, data: {} } as AxiosError["response"];
            const ref = { message: "" };
            module.exports.default({}, ref).handle(error);
            assert.equal(ref.message, errors.Forbidden);
            await module.exports.default({}).handleAsync(error);
            assert.equal(toasts.pop(), errors.Forbidden);
        });
        await context.test("async messages become visible before resolved after callback", async () => {
            const error = new AxiosError("Private server detail");
            const validationErrors = { title: { invalid: ["Private validation detail"] } };
            error.response = { status: 500, data: { errors: validationErrors } } as AxiosError["response"];
            for (const output of ["reference", "toast"]) {
                const events: string[] = [];
                const ref = { message: "" };
                const { handleAsync } = module.exports.default(
                    {
                        500: {
                            message: async (receivedError, responseErrors) => {
                                assert.equal(receivedError, error);
                                assert.equal(responseErrors, validationErrors);
                                events.push("message-start");
                                await Promise.resolve();
                                events.push("message-ready");
                                return errors["Internal server error"];
                            },
                            toast: true,
                            after: async (message, receivedError, responseErrors) => {
                                assert.equal(message, errors["Internal server error"]);
                                assert.equal(receivedError, error);
                                assert.equal(responseErrors, validationErrors);
                                assert.equal(output === "reference" ? ref.message : toasts.at(-1), message);
                                events.push("after-start");
                                await Promise.resolve();
                                events.push("after-ready");
                            },
                        },
                    },
                    output === "reference" ? ref : undefined
                );
                await handleAsync(error);
                assert.deepEqual(events, ["message-start", "message-ready", "after-start", "after-ready"]);
                assert.equal(output === "reference" ? ref.message : toasts.pop(), errors["Internal server error"]);
            }
        });
        await context.test("explicit overrides and synchronous callback order remain intact", async () => {
            const error = new AxiosError("Private denial detail");
            error.response = { status: 403, data: {} } as AxiosError["response"];
            for (const message of ["", undefined, () => undefined, errors["Network error"]]) {
                const ref = { message: "unchanged" };
                let afterCalls = 0;
                const handler = module.exports.default(
                    {
                        403: {
                            message,
                            toast: false,
                            after: (value) => {
                                afterCalls++;
                                assert.equal(value, typeof message === "function" ? undefined : message);
                                assert.equal(ref.message, typeof message === "string" && message ? message : "unchanged");
                            },
                        },
                    },
                    ref
                );
                assert.equal(
                    handler.handle(error),
                    typeof message === "string" && message ? undefined : typeof message === "function" ? undefined : message
                );
                assert.equal(afterCalls, 1);
                await handler.handleAsync(error);
                assert.equal(afterCalls, 2);
            }
            const noToast = module.exports.default({ 403: { message: errors.Forbidden, toast: false } });
            assert.equal(noToast.handle(error), errors.Forbidden);
            assert.deepEqual(toasts, []);

            error.response = { status: 403, data: { code: "PE2006" } } as AxiosError["response"];
            const ref = { message: "" };
            module.exports.default({ code: { message: "code override" }, 403: { message: "status override" } }, ref).handle(error);
            assert.equal(ref.message, "status override");
            module.exports.default({ code: { message: "code override" } }, ref).handle(error);
            assert.equal(ref.message, "code override");
        });
        await context.test("cancellation invokes no message, after, or toast in either API", async () => {
            let callbacks = 0;
            const handler = module.exports.default({
                nonApi: {
                    message: () => {
                        callbacks++;
                        return errors["Internal server error"];
                    },
                    after: () => {
                        callbacks++;
                    },
                    toast: true,
                },
            });
            assert.equal(handler.handle(new CanceledError()), undefined);
            assert.equal(await handler.handleAsync(new CanceledError()), undefined);
            assert.equal(callbacks, 0);
            assert.deepEqual(toasts, []);
        });
        for (const [code, expected] of [
            ["PE2006", errors.requests.PE2006],
            ["UNRECOGNIZED_CODE", errors["Internal server error"]],
        ]) {
            const error = new AxiosError();
            error.response = { status: 403, data: { code } } as AxiosError["response"];
            const ref = { message: "" };
            module.exports.default({}, ref).handle(error);
            let redirects = 0;
            const messageHandler = (
                module.exports as unknown as {
                    createApiErrorMessageHandler: (onForbidden: () => void) => (error: unknown) => string;
                }
            ).createApiErrorMessageHandler(() => {
                redirects += 1;
            });
            assert.equal(messageHandler(error), expected);
            assert.equal(redirects, 1);
            assert.equal(messageHandler(new Error("Private detail")), errors["Internal server error"]);
            assert.equal(messageHandler(new CanceledError()), "");
            assert.equal(redirects, 1);
            assert.ok(expected);
            assert.equal(ref.message, expected);
            assert.ok(!ref.message.includes("errors.requests."));
        }
        for (const code of [AxiosError.ERR_NETWORK, AxiosError.ECONNABORTED, AxiosError.ETIMEDOUT]) {
            const ref = { message: "" };
            module.exports.default({}, ref).handle(new AxiosError("Private transport detail", code));
            assert.equal(ref.message, errors["Network error"]);

            const serverError = new AxiosError("Private server detail", code);
            serverError.response = { status: 500, data: {} } as AxiosError["response"];
            module.exports.default({}, ref).handle(serverError);
            assert.equal(ref.message, errors["Internal server error"]);
        }
    });
}
