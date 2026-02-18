# Localy Backend

FastAPI-based backend for the Localy multi-service platform.

## Features

- ?? Hotels & Lodges
- ?? Ticket Sales (Events, Transport)
- ??? Property Listings
- ??? Service Providers
- ??? E-commerce (Products)
- ?? Food & Restaurants
- ?? Health Services (Doctors, Pharmacies, Labs)
- ?? Delivery & Logistics
- ?? Real-time Chat
- ? Reviews & Ratings
- ?? Reels & Stories
- ?? Jobs & Vacancies

## Tech Stack

- **Framework:** FastAPI
- **Database:** PostgreSQL + PostGIS
- **ORM:** SQLAlchemy
- **Cache:** Redis
- **Task Queue:** Celery
- **Storage:** MinIO/S3
- **Authentication:** JWT

## Setup

1. Clone the repository
2. Copy \.env.example\ to \.env\
3. Install dependencies: \pip install -r requirements.txt\
4. Run migrations: \lembic upgrade head\
5. Start the server: \uvicorn app.main:app --reload\

## Project Structure

See the folder structure in the codebase for detailed organization.

## License

Proprietary
