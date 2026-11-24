#include <QCoreApplication>
#include <QFile>
#include <QJsonArray>
#include <QJsonObject>
#include <QDebug>
#include <QTimer>
#include <QSettings>
#include "myjsonparse.h"
#include "weatherdata.h"
#include "mymodel.h"

static void require(bool ok, const char *message)
{
    if (!ok) qFatal("%s", message);
}

int main(int argc, char **argv)
{
    QCoreApplication app(argc, argv);
    require(argc >= 2, "Pass the captured API response path");
    QFile file(QString::fromLocal8Bit(argv[1]));
    require(file.open(QIODevice::ReadOnly), "Cannot open fixture");
    const QJsonDocument fixture = QJsonDocument::fromJson(file.readAll());
    MyJsonParse parser(fixture);
    require(parser.isValid(), "Live Xuanhua response must be accepted");
    require(parser.getCurrentCity() == QStringLiteral("张家口市 宣化区"), "Wrong city");
    auto weather = parser.getWeatherList();
    require(weather.size() == 5, "UI must receive exactly five days");
    qDeleteAll(weather);

    auto root = fixture.object();
    auto data = root.value("data").toObject();
    auto days = data.value("forecast").toArray();
    auto first = days.at(0).toObject();
    first["high"] = QStringLiteral("高温 -12℃");
    first["low"] = QStringLiteral("低温 -23℃");
    days[0] = first;
    data["forecast"] = days;
    root["data"] = data;
    parser.setJsonDocument(QJsonDocument(root));
    require(parser.isValid(), "Valid winter temperatures rejected");
    weather = parser.getWeatherList();
    require(weather.at(0)->maxTemperature() == -12, "Negative high was truncated");
    require(weather.at(0)->minTemperature() == -23, "Negative low was truncated");
    qDeleteAll(weather);

    first["low"] = QStringLiteral("低温 未知");
    days[0] = first;
    data["forecast"] = days;
    root["data"] = data;
    parser.setJsonDocument(QJsonDocument(root));
    require(!parser.isValid(), "Invalid temperature must not become zero");
    require(parser.getWeatherList().isEmpty(), "Invalid response exposed rows");

    root = fixture.object();
    data = root.value("data").toObject();
    days = data.value("forecast").toArray();
    while (days.size() > 4) days.removeLast();
    data["forecast"] = days;
    root["data"] = data;
    parser.setJsonDocument(QJsonDocument(root));
    require(!parser.isValid(), "Incomplete forecast can crash the five-day UI");

    parser.setJsonDocument(fixture);
    require(parser.isValid(), "Failed to recover after invalid response");
    parser.setJsonDocument(QJsonDocument());
    require(!parser.isValid(), "Null response retained old success state");
    root = fixture.object();
    root["status"] = 500;
    parser.setJsonDocument(QJsonDocument(root));
    require(!parser.isValid(), "Server error must not be displayed as success");
    qInfo("WEATHER_PARSER_TESTS_PASSED");
    if (argc == 3) {
        const bool expectError = QString::fromLocal8Bit(argv[2]) == "--expect-error";
        QSettings settings("Settings/local.ini", QSettings::IniFormat);
        settings.setValue("weather/cityCode", expectError ? "999999999" : "101090302");
        settings.sync();
        MyModel model;
        QObject::connect(&model, &MyModel::statusMessageChanged, &app, [&]() {
            if (model.loading()) return;
            if (expectError) {
                if (!model.ready() && !model.statusMessage().isEmpty()) {
                    qInfo("WEATHER_ERROR_STATE_TEST_PASSED");
                    app.exit(0);
                } else app.exit(2);
            } else if (model.ready()) {
                qInfo().noquote() << "WEATHER_LIVE_TEST_PASSED" << model.city() << model.cTemp();
                app.exit(0);
            } else app.exit(2);
        });
        QTimer::singleShot(20000, &app, [&]() { app.exit(3); });
        const int result = app.exec();
        settings.remove("weather/cityCode");
        settings.sync();
        return result;
    }
    return 0;
}
