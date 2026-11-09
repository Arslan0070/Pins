from concurrent.futures import ProcessPoolExecutor, as_completed
import csv
import json
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont
import time

def load_config():
    with open('boards.json', 'r', encoding='utf-8') as f:
        return json.load(f)

def generate_single_pin(pin_data, font_path, output_dir):
    """Generates a single pin image concurrently to maximize speed."""
    try:
        width, height = 800, 1200
        image = Image.new('RGB', (width, height), color=(255, 255, 255))
        draw = ImageDraw.Draw(image)
        
        try:
            font_title = ImageFont.truetype(font_path, 48)
            font_subtitle = ImageFont.truetype(font_path, 24)
        except Exception:
            font_title = ImageFont.load_default()
            font_subtitle = ImageFont.load_default()
            
        title = pin_data.get('title', 'Pin Title')
        draw.text((50, 100), title, fill=(0, 0, 0), font=font_title)
        
        pin_id = pin_data.get('id', str(time.time()))
        output_path = Path(output_dir) / f"{pin_id}.png"
        image.save(output_path, 'PNG', optimize=True)
        
        return {
            "id": pin_id,
            "title": title,
            "filename": f"{pin_id}.png",
            "status": "success"
        }
    except Exception as e:
        print(f"Error generating pin: {e}")
        return None

def main():
    start_time = time.time()
    config = load_config()
    
    output_dir = Path('generated_pins')
    output_dir.mkdir(parents=True, exist_ok=True)
    
    font_path = 'assets/Poppins-Bold.ttf'
    pins = config.get('pins', [])
    
    if not pins:
        print("No pins found in configuration.")
        return
        
    print(f"Starting lightning-fast parallel generation of {len(pins)} pins using all CPU cores...")
    
    results = []
    with ProcessPoolExecutor() as executor:
        futures = [executor.submit(generate_single_pin, pin, font_path, output_dir) for pin in pins]
        
        for future in as_completed(futures):
            res = future.result()
            if res:
                results.append(res)
                
    csv_path = Path('generated_pins') / 'pins_report.csv'
    with open(csv_path, mode='w', newline='', encoding='utf-8') as csv_file:
        fieldnames = ['id', 'title', 'filename', 'status']
        writer = csv.DictWriter(csv_file, fieldnames=fieldnames)
        writer.writeheader()
        for row in results:
            writer.writerow(row)
            
    print(f"Successfully generated {len(results)}/{len(pins)} pins and CSV report in {time.time() - start_time:.2f} seconds.")

if __name__ == '__main__':
    main()
