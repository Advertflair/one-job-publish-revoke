-------------------------- MODULE CrashRecovery --------------------------
EXTENDS Naturals

CONSTANT LoseDurableWrite
VARIABLES step, record, firstDecision, writes, cut, firstAfter,
          retryReply, revokeReply

Running == [state |-> "Running", blob |-> "", digest |-> "", recipient |-> ""]
Published == [state |-> "Published", blob |-> "Blob",
              digest |-> "Digest", recipient |-> "Recipient"]
Revoked == [Running EXCEPT !.state = "Revoked"]
Vars == <<step, record, firstDecision, writes, cut, firstAfter,
          retryReply, revokeReply>>

Init ==
    /\ step = "BeforeWrite"
    /\ record = Running
    /\ firstDecision = Running
    /\ writes = 0
    /\ cut = "Pending"
    /\ firstAfter = "Pending"
    /\ retryReply = "Pending"
    /\ revokeReply = "Pending"

Decide(value) ==
    /\ record = Running
    /\ record' = value
    /\ firstDecision' = IF writes = 0 THEN value ELSE firstDecision
    /\ writes' = writes + 1

WriteOriginal ==
    /\ step = "BeforeWrite"
    /\ step' = "AfterWrite"
    /\ Decide(Published)
    /\ UNCHANGED <<cut, firstAfter, retryReply, revokeReply>>

Crash ==
    /\ step \in {"BeforeWrite", "AfterWrite"}
    /\ cut' = step
    /\ step' = "Down"
    /\ record' = IF LoseDurableWrite THEN Running ELSE record
    /\ UNCHANGED <<firstDecision, writes, firstAfter, retryReply, revokeReply>>

Recover ==
    /\ step = "Down"
    /\ step' = "Recovered"
    /\ UNCHANGED <<record, firstDecision, writes, cut, firstAfter,
                   retryReply, revokeReply>>

MarkFirst(operation) ==
    firstAfter' = IF firstAfter = "Pending" THEN operation ELSE firstAfter

Retry ==
    /\ step = "Recovered"
    /\ retryReply = "Pending"
    /\ IF record = Running THEN Decide(Published)
       ELSE UNCHANGED <<record, firstDecision, writes>>
    /\ retryReply' = IF record = Revoked THEN "AlreadyRevoked" ELSE "Published"
    /\ MarkFirst("Retry")
    /\ UNCHANGED <<step, cut, revokeReply>>

Revoke ==
    /\ step = "Recovered"
    /\ revokeReply = "Pending"
    /\ IF record = Running THEN Decide(Revoked)
       ELSE UNCHANGED <<record, firstDecision, writes>>
    /\ revokeReply' = IF record = Published THEN "AlreadyPublished" ELSE "Revoked"
    /\ MarkFirst("Revoke")
    /\ UNCHANGED <<step, cut, retryReply>>

Next == WriteOriginal \/ Crash \/ Recover \/ Retry \/ Revoke \/ UNCHANGED Vars
Spec == Init /\ [][Next]_Vars

DurableDecisionSurvives ==
    /\ writes \in 0..1
    /\ IF writes = 0 THEN record = Running ELSE record = firstDecision

RepliesMatchDecision ==
    /\ (retryReply = "Published" => record = Published)
    /\ (retryReply = "AlreadyRevoked" => record = Revoked)
    /\ (revokeReply = "Revoked" => record = Revoked)
    /\ (revokeReply = "AlreadyPublished" => record = Published)

Completed == retryReply # "Pending" /\ revokeReply # "Pending"

RecoveryRaceContract ==
    Completed =>
        /\ writes = 1
        /\ IF cut = "BeforeWrite" /\ firstAfter = "Revoke"
           THEN /\ record = Revoked
                /\ retryReply = "AlreadyRevoked"
                /\ revokeReply = "Revoked"
           ELSE /\ record = Published
                /\ retryReply = "Published"
                /\ revokeReply = "AlreadyPublished"

NoBeforeRetryFirst == ~(Completed /\ cut = "BeforeWrite" /\ firstAfter = "Retry")
NoBeforeRevokeFirst == ~(Completed /\ cut = "BeforeWrite" /\ firstAfter = "Revoke")
NoAfterRetryFirst == ~(Completed /\ cut = "AfterWrite" /\ firstAfter = "Retry")
NoAfterRevokeFirst == ~(Completed /\ cut = "AfterWrite" /\ firstAfter = "Revoke")
=============================================================================
