#!/usr/bin/env python3
"""
Test script to verify the background scraping solution works correctly.
This script simulates the new background job system.
"""

import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from app import ScrapingJob, get_db_connection
import json
import time

def test_background_scraping():
    """Test the background scraping system"""
    print("🧪 Testing background scraping system...")
    
    # Test data
    test_user_id = 1
    test_keyword = "veterinária"
    test_locations = ["Curitiba", "São Paulo", "Rio de Janeiro"]
    test_total = 50
    
    print(f"📝 Creating test job...")
    print(f"   Keyword: {test_keyword}")
    print(f"   Locations: {test_locations}")
    print(f"   Total results: {test_total}")
    
    # Create job
    job_id = ScrapingJob.create(
        user_id=test_user_id,
        keyword=test_keyword,
        locations=test_locations,
        total_results=test_total
    )
    
    print(f"✅ Job created with ID: {job_id}")
    
    # Check job status
    job = ScrapingJob.get_by_id(job_id)
    print(f"📊 Initial status: {job['status']}")
    print(f"📊 Initial progress: {job['progress']}%")
    
    # Simulate job updates
    print("🔄 Simulating job progress...")
    ScrapingJob.update_status(job_id, 'running', 0, test_locations[0])
    time.sleep(1)
    
    ScrapingJob.update_status(job_id, 'running', 33, test_locations[1])
    time.sleep(1)
    
    ScrapingJob.update_status(job_id, 'running', 66, test_locations[2])
    time.sleep(1)
    
    # Complete job
    ScrapingJob.set_results(job_id, "/test/path/results.csv")
    ScrapingJob.update_status(job_id, 'completed', 100)
    
    # Check final status
    final_job = ScrapingJob.get_by_id(job_id)
    print(f"✅ Final status: {final_job['status']}")
    print(f"✅ Final progress: {final_job['progress']}%")
    print(f"✅ Results path: {final_job['results_path']}")
    
    # Test user jobs retrieval
    user_jobs = ScrapingJob.get_by_user_id(test_user_id, limit=5)
    print(f"📋 User has {len(user_jobs)} jobs")
    
    print("🎉 Background scraping test completed successfully!")
    return True

if __name__ == "__main__":
    try:
        test_background_scraping()
    except Exception as e:
        print(f"❌ Test failed: {e}")
        sys.exit(1)
