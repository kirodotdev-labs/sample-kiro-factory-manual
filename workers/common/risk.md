# Risk

Every item triage routes carries one risk level as a label: how much harm the change could do if it
ships wrong. An item triage did not label (one routed by hand, for example) stays unlabeled. Lowest to highest: very low, low, medium, high, very high (`{{label.riskVeryLow}}`,
`{{label.riskLow}}`, `{{label.riskMedium}}`, `{{label.riskHigh}}`, `{{label.riskVeryHigh}}`). The
Manual's steps set it and say why, and none acts on it: the labels and those comments are a history
to audit. An instance may act on a level in its own skills.

**Reversibility.** A choice is hard to undo when, once merged or released, it changes persisted
data, a public interface, user data or security behavior; the instance's rules may add more.
Everything else can be undone by an ordinary change.

## Who sets it
- **Triage** sets the first level from the rubric below, on a fully triaged item (Case B).
- **The `risk` signal** re-judges it in every step that lists it in its verification: by default
  implement, before it flips a PR to ready, and release. The step applies a raise or lower at most
  once per PR head. It never sets a first level: an unlabeled item stays unlabeled.
- **Recording.** Every factory change of level is one board write that swaps the label and says the
  new level and why, in triage's comment or the step's status comment. The level the factory last
  stated is its level of record.
- **A person's level is final.** A label that differs from the factory's level of record was set by
  a person. The factory never changes it. When its evidence disagrees, the status comment says so in
  one line.

## Triage's rubric
Take the highest level that applies. A `risk-levels` heading in an instance skill adds mappings, for
example for its own axes or verification levels, and may replace these.
- **very high**: two or more of the high triggers at once, or a `notable` security finding touching
  authentication, authorization, secrets or a sandbox.
- **high**: design `full`, a spec the domain owners review, a `notable` security finding, verification at the top of
  the ladder, or a change that is hard to undo.
- **medium**: design `spec`, or verification above the fast-track ceiling.
- **low**: design `none` with verification at the fast-track ceiling.
- **very low**: design `none`, security `none` and verification at the lowest level.
