----------------------------- MODULE OneJob -----------------------------
CONSTANT Mode
VARIABLES phase, sealed, committed, publishReply, revokeReply,
          checked, revokedEver, readLog

None == [blob |-> "", digest |-> "", audience |-> ""]
Pending == "Pending"
Recipient == "Recipient"
Outsider == "Outsider"
Readers == {Recipient, Outsider}
Output == [blob |-> "Blob", digest |-> "Digest", audience |-> Recipient]
OtherBinding == [Output EXCEPT !.digest = "OtherDigest"]
Bindings == {Output, OtherBinding}
Vars == <<phase, sealed, committed, publishReply, revokeReply,
          checked, revokedEver, readLog>>

Init ==
    /\ phase = "Running"
    /\ sealed = None
    /\ committed = None
    /\ publishReply = Pending
    /\ revokeReply = Pending
    /\ checked = FALSE
    /\ revokedEver = FALSE
    /\ readLog = {}

Seal ==
    /\ phase = "Running"
    /\ sealed = None
    /\ sealed' = Output
    /\ UNCHANGED <<phase, committed, publishReply, revokeReply,
                   checked, revokedEver, readLog>>

PublishResult(authorized, expected) ==
    CASE ~authorized -> "Denied"
      [] phase = "Revoked" -> "AlreadyRevoked"
      [] phase = "Published" ->
         IF expected = committed THEN "Published" ELSE "Conflict"
      [] sealed = None -> "NotReady"
      [] expected # sealed -> "DigestMismatch"
      [] OTHER -> "Published"

RevokeResult(authorized) ==
    CASE ~authorized -> "Denied"
      [] phase = "Published" -> "AlreadyPublished"
      [] OTHER -> "Revoked"

Publish(expected) ==
    /\ publishReply = Pending
    /\ ~checked
    /\ LET result == PublishResult(TRUE, expected)
           wins == phase = "Running" /\ result = "Published"
       IN IF Mode = "SplitPublish" /\ wins
          THEN /\ checked' = TRUE
               /\ UNCHANGED <<phase, committed, publishReply>>
          ELSE /\ phase' = IF wins THEN "Published" ELSE phase
               /\ committed' = IF wins THEN expected ELSE committed
               /\ publishReply' = result
               /\ UNCHANGED checked
    /\ UNCHANGED <<sealed, revokeReply, revokedEver, readLog>>

FinishSplitPublish ==
    /\ Mode = "SplitPublish"
    /\ checked
    /\ publishReply = Pending
    /\ phase' = "Published"
    /\ committed' = sealed
    /\ publishReply' = "Published"
    /\ checked' = FALSE
    /\ UNCHANGED <<sealed, revokeReply, revokedEver, readLog>>

Revoke ==
    /\ revokeReply = Pending
    /\ revokeReply' = RevokeResult(TRUE)
    /\ phase' = IF phase = "Running" THEN "Revoked" ELSE phase
    /\ revokedEver' = (revokedEver \/ phase' = "Revoked")
    /\ UNCHANGED <<sealed, committed, publishReply, checked, readLog>>

ReadReply(code, payload) == [code |-> code, payload |-> payload]

ReadResult(reader, exists) ==
    CASE reader # Recipient \/ ~exists -> ReadReply("Denied", None)
      [] Mode = "DirectRead" /\ sealed # None -> ReadReply("Released", sealed)
      [] phase # "Published" -> ReadReply("NotPublished", None)
      [] committed = None \/ committed # sealed -> ReadReply("Unavailable", None)
      [] OTHER -> ReadReply("Released", committed)

StatusResult(reader, exists) ==
    IF reader # Recipient \/ ~exists THEN "Denied" ELSE phase

Read(reader) ==
    LET observation == [who |-> reader, at |-> phase,
                        binding |-> committed, reply |-> ReadResult(reader, TRUE)]
    IN /\ observation \notin readLog
       /\ readLog' = readLog \cup {observation}
       /\ UNCHANGED <<phase, sealed, committed, publishReply, revokeReply,
                      checked, revokedEver>>

Next ==
    \/ Seal
    \/ \E expected \in Bindings : Publish(expected)
    \/ FinishSplitPublish
    \/ Revoke
    \/ \E reader \in Readers : Read(reader)
    \/ UNCHANGED Vars

Spec == Init /\ [][Next]_Vars

RevocationSticks == revokedEver => phase = "Revoked"

PublicationBinds ==
    IF phase = "Published"
    THEN committed = Output /\ sealed = committed /\ publishReply = "Published"
    ELSE committed = None

ReleaseRequiresCommit ==
    \A event \in readLog :
        event.reply.payload # None =>
            /\ event.who = Recipient
            /\ event.at = "Published"
            /\ event.binding = event.reply.payload

UniformDenial ==
    /\ \A exists \in BOOLEAN :
          /\ ReadResult(Outsider, exists) = ReadReply("Denied", None)
          /\ StatusResult(Outsider, exists) = "Denied"
    /\ ReadResult(Recipient, FALSE) = ReadReply("Denied", None)
    /\ StatusResult(Recipient, FALSE) = "Denied"
    /\ RevokeResult(FALSE) = "Denied"
    /\ \A expected \in Bindings : PublishResult(FALSE, expected) = "Denied"

TerminalReplies ==
    /\ (phase = "Published" =>
          /\ PublishResult(TRUE, Output) = "Published"
          /\ PublishResult(TRUE, OtherBinding) = "Conflict"
          /\ RevokeResult(TRUE) = "AlreadyPublished")
    /\ (phase = "Revoked" =>
          /\ \A expected \in Bindings :
                PublishResult(TRUE, expected) = "AlreadyRevoked")
    /\ (phase = "Revoked" => RevokeResult(TRUE) = "Revoked")

NoPublishedWitness ==
    ~(phase = "Published" /\ revokeReply = "AlreadyPublished"
      /\ \E event \in readLog : event.reply = ReadReply("Released", Output))

NoRevokedWitness ==
    ~(phase = "Revoked" /\ publishReply = "AlreadyRevoked"
      /\ \E event \in readLog :
             event.who = Recipient /\ event.at = "Revoked"
             /\ event.reply = ReadReply("NotPublished", None))
=============================================================================
