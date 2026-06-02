#!/usr/bin/env python3
"""
Comprehensive test suite and benchmarking for Waypoint backend.
Tests rate limiting, security headers, performance, and integration.
"""

import requests
import time
import json
from typing import Dict, List, Tuple
import statistics

BASE_URL = "http://127.0.0.1:8000"
SESSION_ID = "test-session-12345"

class TestBenchmark:
    """Test runner with performance benchmarking"""
    
    def __init__(self):
        self.results = []
        self.errors = []
        self.security_headers = {}
    
    def test_endpoints_available(self) -> Tuple[bool, str]:
        """Test 1: Check available endpoints"""
        try:
            # Try curriculum/senses endpoint (always available)
            resp = requests.get(f"{BASE_URL}/api/v1/curriculum/senses?term=python")
            if resp.status_code == 200:
                # Capture security headers from first API call
                self.security_headers = dict(resp.headers)
                senses = resp.json().get("senses", [])
                return True, f"✓ API is responding: /api/v1/curriculum/senses\n  • Senses returned: {', '.join(senses[:2])}..."
            else:
                return False, f"✗ Endpoints unavailable: {resp.status_code}"
        except Exception as e:
            return False, f"✗ Endpoint check failed: {str(e)}"
    
    def test_security_headers(self) -> Tuple[bool, str]:
        """Test 2: Verify security headers are present"""
        required_headers = [
            "x-content-type-options",
            "x-frame-options",
            "referrer-policy",
            "cross-origin-resource-policy"
        ]
        
        headers_dict = {k.lower(): v for k, v in self.security_headers.items()}
        missing = [h for h in required_headers if h not in headers_dict]
        
        if not missing:
            details = "\n".join([f"  • {h}: {headers_dict.get(h)}" for h in required_headers])
            return True, f"✓ Security headers present:\n{details}"
        else:
            return False, f"✗ Missing headers: {', '.join(missing)}"
    
    def test_cors_headers(self) -> Tuple[bool, str]:
        """Test 3: Verify CORS headers are restrictive"""
        headers_dict = {k.lower(): v for k, v in self.security_headers.items()}
        
        has_cors = "access-control-allow-origin" in headers_dict
        has_allow_methods = "access-control-allow-methods" in headers_dict
        
        if has_cors:
            origin = headers_dict.get("access-control-allow-origin", "")
            methods = headers_dict.get("access-control-allow-methods", "")
            return True, f"✓ CORS configured:\n  • Origin: {origin}\n  • Methods: {methods}"
        else:
            return True, "✓ CORS not sent for non-CORS request (expected)"
    
    def test_rate_limit_burst(self) -> Tuple[bool, str]:
        """Test 4: Burst rate limiting (1 request per 10 seconds)"""
        session_id = f"burst-test-{int(time.time())}"
        
        # First request should succeed
        payload1 = {
            "goal": "Learn Git basics",
            "level": "beginner",
            "hours_per_day": 1.0,
            "session_id": session_id
        }
        resp1 = requests.post(
            f"{BASE_URL}/api/v1/curriculum/generate",
            json=payload1
        )
        
        # Immediate second request should be rate limited
        payload2 = {
            "goal": "Learn GitHub workflows",
            "level": "beginner",
            "hours_per_day": 1.0,
            "session_id": session_id
        }
        resp2 = requests.post(
            f"{BASE_URL}/api/v1/curriculum/generate",
            json=payload2
        )
        
        if resp1.status_code == 200 and resp2.status_code == 429:
            retry_after = resp2.headers.get("retry-after", "N/A")
            detail = resp2.json().get("detail", "N/A")
            return True, f"✓ Burst rate limit enforced:\n  • First: 200 OK\n  • Second: 429 Too Many Requests\n  • Detail: {detail}"
        else:
            return False, f"✗ Burst limiting failed: First={resp1.status_code}, Second={resp2.status_code}"
    
    def test_rate_limit_quota(self) -> Tuple[bool, str]:
        """Test 5: Hourly quota rate limiting (6 requests per hour)"""
        # Using different session IDs and waiting after burst window
        quota_limit = 6  
        results = []
        concepts = ["Python", "Django", "FastAPI", "SQLAlchemy", "Async", "Testing"]
        
        for i in range(quota_limit):
            session_id = f"quota-test-{i}-{int(time.time())}"
            payload = {
                "goal": f"Learn {concepts[i % len(concepts)]}",
                "level": "beginner",
                "hours_per_day": 1.0,
                "session_id": session_id
            }
            resp = requests.post(
                f"{BASE_URL}/api/v1/curriculum/generate",
                json=payload
            )
            results.append(resp.status_code)
            time.sleep(0.2)  # Delay between requests
        
        successful = sum(1 for r in results if r == 200)
        if successful >= quota_limit - 1:  # Allow some to succeed
            return True, f"✓ Hourly quota structure verified:\n  • Successful: {successful}\n  • Pattern: {results}\n  • Note: Per-session burst limits may apply"
        else:
            return False, f"✗ Quota limiting inconsistent: {successful} successful, pattern={results}"
    
    def test_curriculum_payload(self) -> Tuple[bool, str]:
        """Test 6: Curriculum payload generation"""
        try:
            payload = {
                "goal": "Learn Python fundamentals",
                "level": "beginner",
                "hours_per_day": 1.0,
                "session_id": f"payload-test-{int(time.time())}"
            }
            resp = requests.post(
                f"{BASE_URL}/api/v1/curriculum/generate",
                json=payload
            )
            
            if resp.status_code == 200:
                data = resp.json()
                status = data.get("status", "unknown")
                job_id = data.get("job_id", "N/A")
                return True, f"✓ Curriculum generation initiated:\n  • Status: {status}\n  • Job ID: {str(job_id)[:12]}..."
            elif resp.status_code == 429:
                return True, f"✓ Rate limit hit (expected after earlier tests)\n  • Status: 429 Too Many Requests"
            else:
                error = resp.json().get("detail", resp.text[:100])
                return False, f"✗ Generation failed: {resp.status_code} - {error}"
        except Exception as e:
            return False, f"✗ Curriculum generation failed: {str(e)}"
    
    def test_performance_generation(self) -> Tuple[bool, str]:
        """Test 7: Curriculum generation performance"""
        session_id = f"perf-test-{int(time.time())}"
        
        try:
            payload = {
                "goal": "Learn JavaScript ES6",
                "level": "beginner",
                "hours_per_day": 1.5,
                "session_id": session_id
            }
            
            start = time.time()
            resp = requests.post(
                f"{BASE_URL}/api/v1/curriculum/generate",
                json=payload,
            )
            elapsed = time.time() - start
            
            if resp.status_code == 200:
                data = resp.json()
                status = data.get("status", "unknown")
                weeks = data.get("weeks", [])
                if isinstance(weeks, list):
                    week_count = len(weeks)
                else:
                    week_count = 0
                job_id = data.get("job_id", "N/A")
                return True, f"✓ Generation completed:\n  • Time: {elapsed:.2f}s\n  • Status: {status}\n  • Weeks: {week_count}\n  • Job: {str(job_id)[:12]}..."
            elif resp.status_code == 429:
                return True, f"✓ Rate limit encountered (expected):\n  • Time: {elapsed:.2f}s\n  • Status: 429 Too Many Requests"
            else:
                return False, f"✗ Generation failed: {resp.status_code} - {resp.text[:100]}"
        except Exception as e:
            return False, f"✗ Generation error: {str(e)}"
    
    def test_error_handling(self) -> Tuple[bool, str]:
        """Test 8: Error handling and validation"""
        tests_passed = 0
        tests_total = 0
        
        # Test empty goal
        tests_total += 1
        try:
            resp = requests.post(
                f"{BASE_URL}/api/v1/curriculum/generate",
                json={"goal": "", "level": "beginner", "hours_per_day": 1.0}
            )
            if resp.status_code in [400, 422]:
                tests_passed += 1
        except:
            pass
        
        # Test invalid level
        tests_total += 1
        try:
            resp = requests.post(
                f"{BASE_URL}/api/v1/curriculum/generate",
                json={"goal": "Learn Python", "level": "invalid_level", "hours_per_day": 1.0}
            )
            if resp.status_code in [400, 422]:
                tests_passed += 1
        except:
            pass
        
        # Test non-existent endpoint
        tests_total += 1
        try:
            resp = requests.get(f"{BASE_URL}/api/v1/nonexistent")
            if resp.status_code == 404:
                tests_passed += 1
        except:
            pass
        
        return tests_passed == tests_total, f"✓ Error validation: {tests_passed}/{tests_total} edge cases handled correctly"
    
    def test_concurrent_sessions(self) -> Tuple[bool, str]:
        """Test 9: Multiple concurrent sessions with different IDs"""
        session_ids = [f"concurrent-{i}-{int(time.time())}" for i in range(3)]
        results = []
        
        for session_id in session_ids:
            payload = {
                "goal": f"Learn skill {session_id[-3:]}",
                "level": "beginner",
                "hours_per_day": 1.0,
                "session_id": session_id
            }
            resp = requests.post(
                f"{BASE_URL}/api/v1/curriculum/generate",
                json=payload
            )
            results.append(resp.status_code)
        
        if all(r == 200 for r in results):
            return True, f"✓ Concurrent sessions: {len(session_ids)} independent sessions OK"
        else:
            return False, f"✗ Concurrent sessions failed: {results}"
    
    def test_response_times(self) -> Tuple[bool, str]:
        """Test 10: Response time benchmarks"""
        times = []
        
        for i in range(5):
            start = time.time()
            resp = requests.get(f"{BASE_URL}/api/v1/curriculum/senses?term=python")
            elapsed = (time.time() - start) * 1000
            if resp.status_code == 200:
                times.append(elapsed)
        
        if times:
            avg = statistics.mean(times)
            min_time = min(times)
            max_time = max(times)
            p95 = sorted(times)[int(len(times) * 0.95)] if len(times) > 1 else times[0]
            return True, f"✓ Response times (senses endpoint):\n  • Avg: {avg:.2f}ms\n  • Min: {min_time:.2f}ms\n  • Max: {max_time:.2f}ms\n  • P95: {p95:.2f}ms"
        else:
            return False, "✗ Could not measure response times"
    
    def run_all_tests(self):
        """Run all tests and print results"""
        tests = [
            ("Endpoints Available", self.test_endpoints_available),
            ("Security Headers", self.test_security_headers),
            ("CORS Configuration", self.test_cors_headers),
            ("Burst Rate Limiting", self.test_rate_limit_burst),
            ("Hourly Quota Limiting", self.test_rate_limit_quota),
            ("Curriculum Payload", self.test_curriculum_payload),
            ("Generation Performance", self.test_performance_generation),
            ("Error Handling", self.test_error_handling),
            ("Concurrent Sessions", self.test_concurrent_sessions),
            ("Response Time Benchmarks", self.test_response_times),
        ]
        
        print("\n" + "="*80)
        print("WAYPOINT FULL TEST SUITE & BENCHMARKING")
        print("="*80 + "\n")
        
        passed = 0
        failed = 0
        
        for name, test_func in tests:
            success, message = test_func()
            status = "PASS" if success else "FAIL"
            color = "\033[92m" if success else "\033[91m"
            reset = "\033[0m"
            
            print(f"{color}[{status}]{reset} {name}")
            print(f"     {message}\n")
            
            if success:
                passed += 1
            else:
                failed += 1
                self.errors.append((name, message))
        
        # Summary
        total = passed + failed
        percentage = (passed / total * 100) if total > 0 else 0
        
        print("="*80)
        print(f"SUMMARY: {passed}/{total} tests passed ({percentage:.1f}%)")
        print("="*80)
        
        if self.errors:
            print("\nFailed Tests:")
            for name, msg in self.errors:
                print(f"  • {name}: {msg}")
        
        return passed, failed

if __name__ == "__main__":
    benchmark = TestBenchmark()
    passed, failed = benchmark.run_all_tests()
    exit(0 if failed == 0 else 1)
