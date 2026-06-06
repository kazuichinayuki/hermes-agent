#!/usr/bin/env python3
"""
MemFail End-to-End Test for Decohere
Based on arXiv:2605.26667 (MemFail: Stress-Testing Failure Modes of LLM Memory Systems)

This script tests the Agent's memory system against 4 canonical failure modes:
1. Summary failure (Conditional-Facts)
2. Storage failure (Coexisting-Facts)
3. Persona-Retrieval (Misleading)
4. Retrieval failure (Long-Hop)
"""

import subprocess
import time
import sys
import json
import uuid

def run_hermes_query(session_name: str, prompt: str) -> str:
    """Run a single-shot query against a named hermes session."""
    cmd = [
        "hermes",
        "-c", session_name,
        "-z", prompt
    ]
    try:
        # Run with a generous timeout, as the LLM needs to respond
        print(f"  [Injecting] {prompt[:80]}...")
        result = subprocess.run(cmd, capture_output=True, text=True, check=True, timeout=120)
        out = result.stdout.strip()
        print(f"  [Response] {out}")
        return out
    except subprocess.CalledProcessError as e:
        print(f"  [Error] hermes CLI failed: {e.stderr}", file=sys.stderr)
        return ""

def wait_for_compression():
    """Wait briefly to ensure decohere background compression triggers and completes."""
    print("  [Waiting 10s for decohere compression to complete...]")
    time.sleep(10)

def evaluate_test(test_name: str, passed: bool, expected: str, actual: str):
    if passed:
        print(f"✅ PASS: {test_name}")
    else:
        print(f"❌ FAIL: {test_name}")
        print(f"   Expected: {expected}")
        print(f"   Actual: {actual}")
    print("-" * 60)
    return passed

def run_all_tests():
    session_name = f"memfail-test-{uuid.uuid4().hex[:6]}"
    print(f"Starting MemFail E2E suite on isolated session: {session_name}\n")
    print("-" * 60)

    results = {}

    # -------------------------------------------------------------------------
    # 1. Summary Failure (Conditional-Facts)
    # Tests if the system strips out conditions during summarization.
    # -------------------------------------------------------------------------
    print("TEST 1: Summary Failure (Conditional-Facts)")
    run_hermes_query(session_name, "Remember this: Jordan hasn't slept in 30 hours. He has a rule: no coffee before 5pm, since it messes with his sleep. Jordan works as a freelance illustrator.")
    wait_for_compression()
    ans1 = run_hermes_query(session_name, "It's 3pm and I'm meeting Jordan -- should I grab him a coffee? Answer with a simple Yes or No and the reason.")
    passed1 = "no" in ans1.lower() and "5pm" in ans1.lower()
    results["Conditional-Facts"] = evaluate_test("Conditional-Facts (Summary Failure)", passed1, "No, because it's before 5pm", ans1)

    # -------------------------------------------------------------------------
    # 2. Storage Failure (Coexisting-Facts)
    # Tests if new preferences overwrite old compatible ones.
    # -------------------------------------------------------------------------
    print("TEST 2: Storage Failure (Coexisting-Facts)")
    run_hermes_query(session_name, "Note about my yoga preferences: I love a slow yin session at the end of a long workweek.")
    wait_for_compression()
    run_hermes_query(session_name, "Another note about yoga: Hot yoga is my go-to whenever I want to really sweat it out.")
    wait_for_compression()
    ans2 = run_hermes_query(session_name, "I'm putting together a yoga schedule for the month—what styles should I rotate through to keep things varied? Please list all the styles I've mentioned.")
    passed2 = "yin" in ans2.lower() and "hot" in ans2.lower()
    results["Coexisting-Facts"] = evaluate_test("Coexisting-Facts (Storage Failure)", passed2, "Both 'yin' and 'hot yoga'", ans2)

    # -------------------------------------------------------------------------
    # 3. Persona-Retrieval (Misleading)
    # Tests if irrelevant distractor queries retrieve wrong memories.
    # -------------------------------------------------------------------------
    print("TEST 3: Persona-Retrieval (Misleading)")
    run_hermes_query(session_name, "Profile on Yuki Tanaka: She spends most mornings hunched over a lightbox, tracing contours from 18th-century wreck surveys. She refuses to eat shellfish because she gets a severe hive reaction. Her studio is lit by a single anglepoise lamp.")
    wait_for_compression()
    ans3 = run_hermes_query(session_name, "Do you know anything about Noah Brooks?")
    # The correct behavior is to abstain (not confuse Noah with Yuki)
    passed3 = "yuki" not in ans3.lower() and "shellfish" not in ans3.lower() and ("don't know" in ans3.lower() or "no information" in ans3.lower() or "not have" in ans3.lower() or "no" in ans3.lower())
    results["Persona-Retrieval"] = evaluate_test("Persona-Retrieval (Misleading / Storage Failure)", passed3, "Abstain / No information about Noah", ans3)

    # -------------------------------------------------------------------------
    # 4. Retrieval Failure (Long-Hop)
    # Tests multi-hop reasoning chain retrieval.
    # -------------------------------------------------------------------------
    print("TEST 4: Retrieval Failure (Long-Hop)")
    run_hermes_query(session_name, "Fact 1: Carlos adores rainy afternoons because they slow his day.")
    run_hermes_query(session_name, "Fact 2: Rainy afternoons prompt Carlos to bake banana bread.")
    run_hermes_query(session_name, "Fact 3: Banana bread makes Carlos post cheerful photos.")
    wait_for_compression()
    ans4 = run_hermes_query(session_name, "On the kind of afternoons Carlos adores, what does he end up doing? Give me the final outcome.")
    passed4 = "photo" in ans4.lower() or "cheerful" in ans4.lower()
    results["Long-Hop"] = evaluate_test("Long-Hop (Retrieval Failure)", passed4, "Post cheerful photos", ans4)

    # Summary
    print("\n--- FINAL RESULTS ---")
    score = sum(1 for v in results.values() if v)
    for k, v in results.items():
        print(f"{'✅' if v else '❌'} {k}")
    print(f"Total Score: {score}/4")
    
    if score == 4:
        print("\nSUCCESS: Decohere passed all MemFail canonical failure tests!")
        sys.exit(0)
    else:
        print("\nWARNING: Decohere exhibited one or more memory failure modes.")
        sys.exit(1)

if __name__ == "__main__":
    run_all_tests()
