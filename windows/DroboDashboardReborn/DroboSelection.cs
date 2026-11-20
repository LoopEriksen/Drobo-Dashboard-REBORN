using System.Collections.Generic;
using System.Text.Json;

namespace DroboDashboardReborn;

/// <summary>
/// What the picker sends to the agent's POST /api/drobo/select when a row's
/// Connect is clicked, and what it makes of the answer.
///
/// Until 2026-11-20 Connect never told the agent which Drobo had been
/// clicked: it opened the dashboard of whatever Drobo the agent already
/// watched. A typed address was probed, reported as found, and then never
/// used, so its dashboard stayed unreachable; and with two Drobos the
/// picker opened the other one (readiness M2 and S8). Kept free of WPF so
/// the test program can check it on any machine.
/// </summary>
public static class DroboSelection
{
    /// <summary>The agent route that points it at a Drobo.</summary>
    public const string Route = "/api/drobo/select";

    /// <summary>How the agent answered a select request.</summary>
    public enum Outcome
    {
        /// <summary>The agent now watches that Drobo; open its dashboard.</summary>
        Selected,

        /// <summary>The agent has no such route: an agent older than this
        /// app. The dashboard opens as it did before, on whichever Drobo
        /// that agent watches, rather than not at all.</summary>
        AgentWithoutRoute,

        /// <summary>The agent refused, and the reason is the sentence to
        /// show. Nothing was changed.</summary>
        Refused,
    }

    /// <summary>
    /// The request body for one picker row: its address, and its serial
    /// when the row has one. The serial is what lets the agent switch to a
    /// Drobo other than the one it is paired with -- the row's serial came
    /// from that device's own greeting, so clicking the row names it -- and
    /// what makes the agent refuse an address that now answers as another
    /// box. A row with no serial (a typed address that did not answer)
    /// sends the address alone.
    ///
    /// <paramref name="chosenByPerson"/> is false for the start-up
    /// reconnect to the remembered Drobo, which then sends the address
    /// alone too. Naming the serial is what lets the agent re-pair with
    /// another Drobo, and that must take a click: with the serial sent, an
    /// app launch could silently move an agent paired with one Drobo onto
    /// another that happened to be remembered, rewriting its pinned serial.
    /// Sent alone, the address is taken only when it is the Drobo the agent
    /// is already paired with (or it has none yet).
    /// </summary>
    public static Dictionary<string, string> RequestBody(string host, string? esaId, bool chosenByPerson = true)
    {
        var body = new Dictionary<string, string> { ["host"] = host.Trim() };
        if (chosenByPerson && !string.IsNullOrWhiteSpace(esaId)) body["esa_id"] = esaId.Trim();
        return body;
    }

    /// <summary>The sentence added to the agent's "paired with another"
    /// refusal of a start-up reconnect, saying what click makes the
    /// switch.</summary>
    public const string ClickToSwitch = "Press \"Connect \u2192\" on its row to watch it instead.";

    /// <summary>
    /// What the answer means, with the sentence to show when it is a
    /// refusal: the agent's own words where it gave some, since it names the
    /// address and both serials, and a plain fallback where it did not.
    /// A start-up reconnect (<paramref name="chosenByPerson"/> false) that
    /// the agent refused as another Drobo also says which click switches,
    /// since that reconnect sent no serial on purpose (see RequestBody).
    /// </summary>
    public static (Outcome outcome, string? problem) ReadAnswer(int statusCode, JsonElement root, bool chosenByPerson = true)
    {
        if (statusCode >= 200 && statusCode < 300) return (Outcome.Selected, null);
        if (statusCode == 404 && JsonHelpers.GetStringOr(root, "path", "") == Route)
            return (Outcome.AgentWithoutRoute, null);
        if (statusCode == 401)
            return (Outcome.Refused, "The agent rejected that token. Enter the right one below.");
        var problem = JsonHelpers.GetStringOr(
            root, "error", $"The agent could not switch to that Drobo (HTTP {statusCode}).");
        if (!chosenByPerson && JsonHelpers.GetStringOr(root, "reason", "") == "paired_with_another")
            problem += " " + ClickToSwitch;
        return (Outcome.Refused, problem);
    }
}
