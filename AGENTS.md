# Cat GPS

Cat gps is a web application that presents the current location of the cats on a map of a house.

The application is built using Python and fastapi and uses uv.

The floorplan is defined by the config.yml file

The server receives current data from a mqtt server

# Scripts
* view mqtt messages:
  * `mqtt sub -h chronos -t espresense/companion/+/attributes -u noskcaj --password 25581612`
* Run anything python:
  * use `uv`
* Run the server:
  * `uv run fastapi dev main.py`

