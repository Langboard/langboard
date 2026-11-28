const { test } = require("node:test");
const assert = require("node:assert/strict");
const { canHandoffCustody } = require("../src/pages/BoardPage/components/settings/appCredentialCustodyState.ts");
const valid = {
    connection_uid: "connection", connection_revision: "revision", current: true, approved_by_me: true,
    custody: { custody_uid: "custody", revision: 2, state: "enabled", destination_uid: "target", destination_revision: 1 },
    destination: { destination_uid: "target", revision: 1, origin: "https://app.example" }, last_handoff: null,
};
test("only current owner-approved custody to the reviewed destination can hand off", () => {
    assert.equal(canHandoffCustody(valid), true);
    for (const value of [undefined, { ...valid, custody: null }, { ...valid, current: false },
        { ...valid, approved_by_me: false }, { ...valid, destination: null },
        { ...valid, destination: { ...valid.destination, destination_uid: "other" } },
        { ...valid, destination: { ...valid.destination, revision: 2 } },
        { ...valid, custody: { ...valid.custody, state: "revoked" } },
    ]) assert.equal(canHandoffCustody(value), false);
});
test("refresh cannot turn any attempted handoff into another issuance for the same approval", () => {
    for (const state of ["prepared", "delivered", "unknown_revoked", "revoked"]) {
        assert.equal(canHandoffCustody({ ...valid, last_handoff: { handoff_uid: "h", state, custody_revision: 2 } }), false);
    }
    assert.equal(canHandoffCustody({ ...valid, last_handoff: { handoff_uid: "h", state: "unknown_revoked", custody_revision: 1 } }), true);
});
