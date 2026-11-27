from .App import App


def create_app():
    app = App()
    return app.create()


app = create_app()
