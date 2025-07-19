import os
from typing import Dict
from venv import logger
from dotenv import load_dotenv
import requests

load_dotenv()


GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
def enhance_with_ai(data: Dict) -> Dict:
    """Use AI to enhance the CV content"""
    try:
        exp_str = "\n".join([f"{e['role']} at {e['company']} ({e['years']}): {e['description']}" 
                       for e in data.get('experience', [])])
        edu_str = "\n".join([f"{e['degree']} at {e['institution']} ({e['years']})" 
                       for e in data.get('education', [])])
        
        prompt = f"""
        Create a professional CV using only this information:
        
        Name: {data.get('name', '')}
        Contact: {data.get('email', '')} | {data.get('phone', '')}
        
        Experience:
        {exp_str}
        
        Education:
        {edu_str}
        
        Skills: {', '.join(data.get('skills', []))}
        Languages: {', '.join(data.get('languages', []))}
        
        Return enhanced content as a dictionary with:
        - summary (3-4 sentence professional summary)
        - experience (enhanced descriptions with achievements)
        - education
        - skills (organized by category)
        - languages
        - certifications (2-3 relevant ones)
        
        Return ONLY a valid Python dictionary with no additional text or explanations.
        The dictionary should be in this exact format:
        {{
            'summary': '...',
            'experience': [...],
            'education': [...],
            'skills': {{...}},
            'languages': [...],
            'certifications': [...]
        }}
        """
        
        response = ask_gemini(prompt)
        
        try:
            # Find the first { and last } to extract just the dictionary
            start = response.find('{')
            end = response.rfind('}') + 1
            dict_str = response[start:end]
            
            enhanced = eval(dict_str)
            if isinstance(enhanced, dict):
                for key in ['summary', 'experience', 'education', 'skills', 'languages', 'certifications']:
                    if key in enhanced:
                        data[key] = enhanced[key]
        except Exception as e:
            logger.warning(f"Couldn't parse AI response: {e}")
            # Fallback: organize skills simply
            if 'skills' in data and isinstance(data['skills'], list):
                data['skills'] = {'technical': data['skills']}
                
    except Exception as e:
        logger.error(f"AI enhancement failed: {e}")
        # Fallback if complete failure
        if 'skills' in data and isinstance(data['skills'], list):
            data['skills'] = {'technical': data['skills']}
    
    return data

def ask_gemini(prompt: str) -> str:
    """Get response from Gemini API"""
    clean_prompt = f"""
    You are a professional CV writer. 
    Only return the requested CV content - no explanations, instructions or additional text.
    
    {prompt}
    """
    
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={GEMINI_API_KEY}"
    headers = {"Content-Type": "application/json"}
    data = {"contents": [{"parts": [{"text": clean_prompt}]}]}

    try:
        response = requests.post(url, headers=headers, json=data, timeout=10)
        response.raise_for_status()
        return response.json()['candidates'][0]['content']['parts'][0]['text']
    except Exception as e:
        logger.error(f"Gemini API Error: {e}")
        return ""
