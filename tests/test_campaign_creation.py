import os
import json
from app import init_db, User, Campaign, CampaignLead, get_db_connection
from datetime import datetime

def test_campaign_flow():
    print("Testing Campaign Flow...")
    
    # 1. User
    email = "test_campaign_user@example.com"
    user = User.get_by_email(email)
    if not user:
        user = User.create(email, "password123")
        print(f"Created user: {user.id}")
    else:
        print(f"Using user: {user.id}")

    # 2. Dummy Scraping Job
    # Create a dummy results file
    results_file = "test_results.json"
    dummy_leads = [
        {"title": "Padaria do Zé", "phone": "(11) 99999-1111", "address": "Rua A"},
        {"title": "Mercado da Maria", "phone": "11988882222", "address": "Rua B"},
        {"title": "Lead Sem Telefone", "phone": "", "address": "Rua C"}
    ]
    with open(results_file, 'w', encoding='utf-8') as f:
        json.dump(dummy_leads, f)
        
    results_path = os.path.abspath(results_file)
    
    conn = get_db_connection()
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO scraping_jobs (user_id, keyword, locations, total_results, status, results_path, created_at)
            VALUES (%s, 'Test Keyword', 'Test Location', 3, 'completed', %s, NOW())
            RETURNING id
            """,
            (user.id, results_path)
        )
        job_id = cur.fetchone()[0]
    conn.commit()
    print(f"Created Job: {job_id}")

    # 3. Create Campaign (Simulate API Logic)
    print("Simulating API Campaign Creation...")
    
    # Logic copied/adapted from app.py
    with open(results_path, 'r', encoding='utf-8') as f:
        leads_data = json.load(f)
        
    import re
    valid_leads = []
    for l in leads_data:
        phone = l.get('phone')
        if phone:
            clean_phone = re.sub(r'\D', '', phone)
            if len(clean_phone) >= 10:
                valid_leads.append({
                    'phone': clean_phone, 
                    'name': l.get('title') or l.get('name') or 'Visitante'
                })
    
    print(f"Valid Leads Found: {len(valid_leads)}")
    
    if valid_leads:
        campaign = Campaign.create(user.id, "Test Campaign", "Hello {name}", 100)
        print(f"Created Campaign: {campaign.id}")
        
        CampaignLead.add_leads(campaign.id, valid_leads)
        print("Leads added to campaign.")
        
        # Verify in DB
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM campaign_leads WHERE campaign_id = %s", (campaign.id,))
            count = cur.fetchone()[0]
            print(f"DB Check: {count} leads in campaign_leads table.")
            
            if count == 2:
                print("SUCCESS: Lead count matches.")
            else:
                print(f"FAILURE: Expected 2 leads, got {count}")
    
    conn.close()
    
    # Cleanup
    if os.path.exists(results_file):
        os.remove(results_file)

if __name__ == "__main__":
    try:
        test_campaign_flow()
    except Exception as e:
        print(f"Test Failed: {e}")
        import traceback
        traceback.print_exc()
