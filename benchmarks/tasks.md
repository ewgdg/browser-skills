# Participant handout

Runner: send the setup and **one turn at a time**, in order. Substitute URL, mode, thread name and environment. Do not give participants `benchmarks/README.md`, fixture implementation or oracle data. Turn boundaries are part of the workload; do not combine them.

## Setup

You are testing MODE against the local fixture at URL. Use only the supplied browser surface (Surf CLI or `surf_agent.Thread`, according to MODE) to inspect and interact with the fixture. Use thread THREAD and the supplied isolated `SURF_AGENT_HOME`. Keep that thread open across turns. Ordinary shell commands and scratch state under `/tmp` are allowed, but fixture HTTP requests outside the browser, fixture source and runner/oracle files are out of bounds. Use concise, selected observations where useful; full snapshots are optional. Report failures honestly and never automatically retry a submission after a timeout.

## Turn 1 — lookup and inspect conditional form

Open the fixture home page. Report the main heading, the destination URL of “Dispatch desk”, and the daily code. Retain the code for later turns. Open “Handoff form”, inspect its instructions and choose the instructed request type to reveal its conditional fields. Report the revealed field labels. Leave the form open without submitting.

## Turn 2 — exact submission and paginated aggregation

Complete the open form according to its instructions: requester, request type, retained daily code, request ID and exact Notes text, preserving quotes, punctuation, Unicode, blank lines and literal characters. Submit exactly once and report its receipt and the stored Notes text shown by the page.

Open the records page from Home. Collect every record across all pages, retaining IDs, amounts and regions for a later question. Report the number of records, sum of all amounts, and ID and amount of the largest record.

## Turn 3 — retained-data follow-up

Using your retained data, without revisiting or rereading the browser pages, report the sum of amounts for the east region and list its record IDs. Keep the thread open for the runner's cleanup or optional recovery check.

## Turn 4 — interpreter-loss recovery (runner resets persistent worker first)

The execution interpreter has been reset if your mode uses one; its variables and handles are gone. Browser and permitted files remain intact. Reattach to your existing named browser thread, report the current page heading, and report the IDs and amounts of every record with amount at least 30. You may re-observe pages or use retained files/conversation; do not submit the form again. Close your benchmark thread when finished. Report any recovery failure honestly.
