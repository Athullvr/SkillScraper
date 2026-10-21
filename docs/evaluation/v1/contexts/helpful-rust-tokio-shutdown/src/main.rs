use std::time::Duration;

#[tokio::main]
async fn main() {
    let worker = tokio::spawn(async {
        loop {
            tokio::time::sleep(Duration::from_secs(30)).await;
        }
    });

    if tokio::signal::ctrl_c().await.is_ok() {
        drop(worker);
    }
}
