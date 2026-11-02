using System;
using System.Net.Http;
using System.Text.Json;

namespace DroboDashboardReborn;

/// <summary>
/// How the agent said a configuration write ended.
///
/// Four cases, not two, because of what a real Drobo 5N did on 2026-08-25: it
/// was sent a share create, closed the connection without ever answering, and
/// had created the share anyway. Every layer above called that a failure. So a
/// write that applied must never be shown as failed, and a write nobody can
/// settle must never be shown as done -- the agent labels every write answer
/// with one of these (its "outcome" field) and this app renders the label
/// rather than inferring it from the status code or the wording.
/// </summary>
public enum WriteOutcome
{
    /// <summary>The Drobo acknowledged the write and it reads back changed.</summary>
    Confirmed,

    /// <summary>The Drobo never acknowledged the write, but it reads back
    /// changed anyway. A success — the read-back is what settles it, not the
    /// acknowledgement — shown green, carrying the agent's own account of the
    /// silence.</summary>
    ConfirmedUnacknowledged,

    /// <summary>The write went out and nothing could settle whether it applied.
    /// Shown as a WARNING, never an error: the change may already be on the
    /// device, and the agent's message says to go and look before trying
    /// again.</summary>
    Unsettled,

    /// <summary>Refused, unreachable, or applied nothing. A plain failure.</summary>
    Failed,
}

/// <summary>
/// The rules for <see cref="WriteOutcome"/>, kept beside the enum so the way
/// an agent answer becomes an outcome, and which outcomes count as done, are
/// read in one place. A C# enum cannot carry methods, hence a static class of
/// extension-style helpers.
/// </summary>
public static class WriteOutcomes
{
    /// <summary>
    /// Read the agent's "outcome" field out of a write answer.
    ///
    /// Defensive in one direction only. An answer with no "outcome" at all is
    /// judged the way this app judged every write before the field existed --
    /// by the HTTP status and "ok" -- and an outcome name this build does not
    /// recognise becomes Unsettled rather than a success, because that is the
    /// reading that cannot mislead: it costs a re-read, where a wrong success
    /// tells the user a change is on their Drobo when it may not be.
    /// </summary>
    public static WriteOutcome FromAgentAnswer(bool isSuccessStatus, JsonElement answer)
    {
        // IsTrue rather than TryGetProperty, which throws when the answer is
        // not an object (a JSON null or a list).
        var statusSaysSucceeded = isSuccessStatus && JsonHelpers.IsTrue(answer, "ok");
        var claimedOutcome = JsonHelpers.GetStringOr(answer, "outcome", "") switch
        {
            "verified" => WriteOutcome.Confirmed,
            "verified_unacknowledged" => WriteOutcome.ConfirmedUnacknowledged,
            "failed" => WriteOutcome.Failed,
            "unknown" => WriteOutcome.Unsettled,
            "" => statusSaysSucceeded ? WriteOutcome.Confirmed : WriteOutcome.Failed,
            _ => WriteOutcome.Unsettled,
        };
        // A success label on an answer whose status says otherwise is a
        // contradiction, and the honest reading of a contradiction is that
        // nobody knows what happened.
        return !statusSaysSucceeded && claimedOutcome.Succeeded() ? WriteOutcome.Unsettled : claimedOutcome;
    }

    /// <summary>Whether the write is one the user can treat as done.</summary>
    public static bool Succeeded(this WriteOutcome outcome) =>
        outcome is WriteOutcome.Confirmed or WriteOutcome.ConfirmedUnacknowledged;

    /// <summary>
    /// How long this app waits for the agent's answer to a write, in
    /// seconds. Reads keep their own, shorter limit.
    ///
    /// It must outlast the agent's own worst case, or the app gives up first
    /// and the agent goes on to settle the write with nobody listening. That
    /// worst case is four connections to the Drobo's command port (the read
    /// before, the write, the read-back, and the fresh read that settles an
    /// unacknowledged write), each of which can wait the agent's 8-second
    /// COMMAND_TIMEOUT_SECONDS three times (connect, login reply, result):
    /// 4 x 3 x 8 = 96 seconds. Until 2026-11-02 writes used the 8 seconds
    /// reads use, the same as one of the agent's own waits; measured on a
    /// simulated device that takes a share create and stays silent, the
    /// agent answered at 8.01 seconds, and at 16.02 with the read-back
    /// stalled too, both after the app had already shown a failure.
    /// </summary>
    public const int AgentWriteTimeoutSeconds = 120;

    /// <summary>
    /// The outcome of a write whose request to the agent failed, so no
    /// answer was read.
    ///
    /// Failed only when the request cannot have reached the agent: it could
    /// not connect or resolve the name, or the request could not be built.
    /// Everything else -- a timeout, a connection that ended after the
    /// request went out, an answer that is not JSON -- is Unsettled, because
    /// the agent may well have sent the write to the Drobo and only its
    /// answer was lost. Until 2026-11-02 every one of these was shown as a
    /// plain failure, in the red kept for "nothing happened", and the share
    /// dialogs stayed ready to send the write again.
    /// </summary>
    public static WriteOutcome FromTransportFailure(Exception failure) => failure switch
    {
        HttpRequestException { HttpRequestError: HttpRequestError.ConnectionError or HttpRequestError.NameResolutionError }
            => WriteOutcome.Failed,
        UriFormatException or InvalidOperationException => WriteOutcome.Failed,
        _ => WriteOutcome.Unsettled,
    };

    /// <summary>The words for a write whose request failed, matching
    /// <see cref="FromTransportFailure"/>: "may have been done" for an
    /// unsettled one, never "could not reach" for a write that may be on
    /// the Drobo.</summary>
    public static string DescribeTransportFailure(Exception failure) =>
        FromTransportFailure(failure) == WriteOutcome.Unsettled
            ? "No answer came back from the agent, so this may have been done on the Drobo. "
              + "Refresh and look before trying again.  (" + failure.Message + ")"
            : "Could not reach the agent.  (" + failure.Message + ")";
}
