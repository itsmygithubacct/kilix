# XDND legacy MOVE and independent display legs

The successor supplies a cooperating source and consuming-client implementation
for genuine version 3/4 MOVE. It preserves MOVE, transfers the complete offered
representation, validates the consuming client's DELETE conversion and
acknowledgement, and only then permits a positive legacy completion. This is an
authored source contract awaiting independent review and toolkit qualification.
It does not narrow the original version 3–5 COPY/MOVE acceptance requirement.

Each display leg negotiates its own version from its own target's XdndAware.
The ordinary selected Qt targets still advertise version 5. An incoming
version 3 source on one private display does not force version 3 on the outgoing
leg. The Qt implementation stores incoming and outgoing versions separately.

| Participant | Successful legacy MOVE obligation | Failure |
| --- | --- | --- |
| SDK or engine as receiver of the original source | Current original owner/acquisition/drop tuple, complete bytes, accepted downstream MOVE, advertised DELETE, exact timestamp/property/type/format/empty acknowledgement | No success-equivalent legacy Finished; no speculative deletion |
| SDK or engine as source toward a destination | Complete successful delivery, same live receiving XRes client, acknowledged DELETE receipt, then matching Finished | Refuse a result without its receipt; preserve MOVE rather than coerce COPY |
| Selected Qt destination | Ordinary accepted drop callback, full direct or INCR payload, exact source epoch, supported DELETE, validated empty NULL/8 acknowledgement before Finished | IgnoreAction; legacy failure withholds Finished |
| Selected Qt source | Advertise DELETE with MOVE, publish full bytes, bind payload and DELETE requestors to the target's XRes client, acknowledge DELETE after delivery, wait for actual Finished before returning the QDrag result | IgnoreAction after refusal, missing receipt, loss/reentry, incomplete publication or bounded wait |

Legacy Finished carries the receiver XID and zero reserved words. The version
5 success/action fields retain their own meaning. Reserved legacy bits never
authorize MOVE. The Qt source's returned QDrag action now depends on the actual
completion transcript rather than a selected action at mouse release. The
original same-process Qt callback path remains distinct from external selection
transport and keeps its ordinary callback result.

DELETE acknowledges consumption of the offered representation. The bridge and
Qt transport do not interpret paths or delete files. GTK's application DELETE
handler and Qt's application use of the returned MoveAction remain the original
source's responsibility. Downstream confirmation, original-source deletion and
their independent epochs are separate steps; this does not claim atomic durable
commit across applications.

The selected source pair covers Qt 5.15.15/5.15.18 and Qt
6.8.2/6.10.3/6.11.2. The corresponding package/vendor records, exact public Git
blobs, applicable Fedora Qt 5.15.18 hunk and private header/build changes are
bound in the successor packet. Rebuild the whole affected private Qt module for
its matching version, including its XCB/XRes dependency. These are selected
source exports, not delivered full toolkit dependency trees or installed ABI
proof. Unpatched peers without a legacy consuming-client result/DELETE contract
remain an interoperability qualification obligation; refusal is not closure.

Ownership checks drain XFixes notifications before and after owner queries,
including same-XID reacquisition. Payload, metadata, checked writes, INCR chunks,
empty terminator, DELETE acknowledgement and Finished are bound to their current
transaction. Data retains its advertised MIME representation, including 8/16/32
formats. DND has a 64 MiB aggregate payload bound, eight payload requests and a
4.5-second transaction/selection wait; ordinary clipboard transfers retain their
existing behavior. A live X server's blocking roundtrips and real callback/event
ordering still require native qualification under the original five-second I4a
requirement.

The source-only Python controls execute the actual SDK and selected quote/drop
functions with memory dependencies. They do not execute Qt, the engine's native
transport, applications, real DELETE handlers, shells or installed libraries.
The compile request tests selected Qt completion/client/epoch functions with
memory dependencies; it does not cover the Qt sender, INCR publisher or receiver
conversion bodies. Full translation units, all toolkits/platforms, actual normal
callbacks and source-visible action consumers, I1–I4d, packaging/installed
pairing and combined RC6 acceptance remain open. No READY or author approval is
implied.

Primary protocol source: [XDND specification](https://wiki.freedesktop.org/www/Specifications/XDND/).
Exact Qt references are the public qtbase tags recorded in the packet's
QT-RETRIEVAL.json; interpretations of QDrag result and callback behavior are
source inferences pending native validation.
