from concurrent.futures import ProcessPoolExecutor, as_completed
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
        
        output_path = Path(output_dir) / f"{pin_data.get('id', time.time())}.png"
        image.save(output_path, 'PNG', optimize=True)
        return True
    except Exception as e:
        print(f"Error generating pin: {e}")
        return False

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
    
    success_count = 0
    with ProcessPoolExecutor() as executor:
        futures = [executor.submit(generate_single_pin, pin, font_path, output_dir) for pin in pins]
        
        for future in as_completed(futures):
            if future.result():
                success_count += 1
                
    print(f"Successfully generated {success_count}/{len(pins)} pins in {time.time() - start_time:.2f} seconds.")

if __name__ == '__main__':
    main()
