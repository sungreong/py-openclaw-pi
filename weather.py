import requests

def get_weather(city_name, api_key):
    base_url = "http://api.openweathermap.org/data/2.5/weather"
    params = {
        'q': city_name,
        'appid': api_key,
        'units': 'metric'
    }
    response = requests.get(base_url, params=params)
    if response.status_code == 200:
        data = response.json()
        main = data['main']
        weather_desc = data['weather'][0]['description']
        print(f"City: {city_name}")
        print(f"Temperature: {main['temp']}°C")
        print(f"Weather: {weather_desc}")
    else:
        print("City not found or API request failed.")

if __name__ == "__main__":
    city = input("Enter city name: ")
    api_key = "YOUR_API_KEY"  # 여기에 발급받은 API 키를 입력하세요
    get_weather(city, api_key)
